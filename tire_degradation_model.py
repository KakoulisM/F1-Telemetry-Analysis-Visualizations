"""
tire_degradation_model.py
--------------------------
Tire degradation model: trains on lap data and predicts future tire performance.

Usage:
    python tire_degradation_model.py train [session] [--year YEAR]
    python tire_degradation_model.py predict <session> <lookahead>
"""

import argparse
import json
import pickle
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

MODEL_DIR = Path("models")
MODEL_PATH = MODEL_DIR / "tire_deg_model.pkl"

TIMEDELTA_PATTERN = r"(\d+) days (\d+):(\d+):(\d+(?:\.\d+)?)"


def _parse_laptime_seconds(val) -> float:
    """Convert FastF1 timedelta string or float to seconds. Returns NaN on failure."""
    if pd.isna(val):
        return float("nan")
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip()
    # "0 days 00:01:22.920000"
    import re
    m = re.match(TIMEDELTA_PATTERN, s)
    if m:
        days, hours, minutes, seconds = m.groups()
        return int(days) * 86400 + int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    # try plain seconds
    try:
        return float(s)
    except ValueError:
        return float("nan")


def _load_laps(telemetry_dir: Path = Path("telemetry_out")) -> pd.DataFrame:
    """Load all *_laps.csv files and return a combined DataFrame with LapTimeSec column."""
    files = list(telemetry_dir.rglob("*_laps.csv"))
    if not files:
        return pd.DataFrame()

    dfs = []
    for f in files:
        try:
            df = pd.read_csv(f)
            # Extract driver from filename: EVENT_SESSION_DRV_laps.csv
            parts = f.stem.rsplit("_", 2)
            if len(parts) >= 2:
                df["Driver"] = parts[-2]
            df["_source_file"] = str(f)
            dfs.append(df)
        except Exception:
            continue

    if not dfs:
        return pd.DataFrame()

    combined = pd.concat(dfs, ignore_index=True)
    combined["LapTimeSec"] = combined["LapTime"].apply(_parse_laptime_seconds)
    return combined


def _filter_race_sim_laps(df: pd.DataFrame, qual_flags_paths: list) -> pd.DataFrame:
    """
    Filter laps DataFrame to only include race simulation laps using qual_flags.csv files.

    qual_flags.csv columns used: event, session, Driver, LapNumber, improved_label
    Keeps rows where improved_label == 'race_sim'.
    Falls back to heuristic_label or predicted columns if improved_label is absent.

    If no qual_flags files are found or none match, returns the original df unchanged
    with a warning (so the model still trains on all data rather than nothing).
    """
    if not qual_flags_paths:
        return df

    flags_dfs = []
    for p in qual_flags_paths:
        p = Path(p)
        if p.exists():
            try:
                flags_dfs.append(pd.read_csv(p))
            except Exception:
                pass

    if not flags_dfs:
        print("[tire_deg] No qual_flags files found — training on all laps (race_sim filter skipped)")
        return df

    flags = pd.concat(flags_dfs, ignore_index=True)

    # Determine which label column to use (prefer most refined)
    label_col = None
    for col in ("improved_label", "predicted", "heuristic_label"):
        if col in flags.columns:
            label_col = col
            break

    if label_col is None:
        print("[tire_deg] No label column found in qual_flags — training on all laps")
        return df

    # Union across all available label columns: a lap is race_sim if ANY column says so.
    # This prevents over-filtering when improved_label is sparse.
    race_sim_mask = pd.Series(False, index=flags.index)
    label_cols_used = []
    for col in ("improved_label", "heuristic_label", "predicted"):
        if col in flags.columns:
            race_sim_mask |= (flags[col] == "race_sim")
            label_cols_used.append(col)
    race_sim_flags = flags[race_sim_mask]
    label_col = "+".join(label_cols_used) if label_cols_used else label_col

    if race_sim_flags.empty:
        print(f"[tire_deg] No race_sim laps found in qual_flags ({label_col}) — training on all laps")
        return df

    # Build merge keys: Driver + LapNumber, matched against source file session context
    # The flags have event/session columns; encode them into a joinable key
    race_sim_flags = race_sim_flags.copy()
    if "LapNumber" in race_sim_flags.columns:
        race_sim_flags["LapNumber"] = pd.to_numeric(race_sim_flags["LapNumber"], errors="coerce")

    # Join on Driver + LapNumber (within the same session, inferred from source file)
    # Build a set of (session_fragment, driver, lap_number) tuples for fast lookup
    sim_keys = set()
    for _, row in race_sim_flags.iterrows():
        driver = str(row.get("Driver", "")).strip()
        lap = row.get("LapNumber")
        event = str(row.get("event", "")).strip()
        session = str(row.get("session", "")).strip()
        if driver and not pd.isna(lap):
            sim_keys.add((event.lower(), session.lower(), driver.upper(), int(lap)))

    def _row_is_race_sim(row):
        driver = str(row.get("Driver", "")).strip().upper()
        lap = row.get("LapNumber")
        src = str(row.get("_source_file", "")).replace("\\", "/").lower()
        if pd.isna(lap):
            return False
        lap = int(float(lap))
        # Extract event/session from source path: telemetry_out/year/event/session/...
        parts = src.split("/")
        # Find telemetry_out anchor
        try:
            idx = next(i for i, p in enumerate(parts) if "telemetry_out" in p)
            event_part = parts[idx + 2].lower() if len(parts) > idx + 2 else ""
            session_part = parts[idx + 3].lower() if len(parts) > idx + 3 else ""
        except StopIteration:
            event_part = ""
            session_part = ""
        return (event_part, session_part, driver, lap) in sim_keys

    mask = df.apply(_row_is_race_sim, axis=1)
    filtered = df[mask]
    n_total = len(df)
    n_filtered = len(filtered)
    print(f"[tire_deg] Race-sim filter: {n_filtered}/{n_total} laps kept (label: {label_col})")

    if filtered.empty:
        print("[tire_deg] Race-sim filter produced empty set — training on all laps as fallback")
        return df

    return filtered


def _filter_session(df: pd.DataFrame, session_prefix: str) -> pd.DataFrame:
    """Filter dataframe to rows matching session_prefix in source file path.

    Handles both direct substring matches (e.g. legacy flat paths) and the
    canonical folder structure where the session name is split across parent
    directories: telemetry_out/{year}/{event}/{session_type}/...
    e.g. prefix 'Australian_Grand_Prix_Practice_1' must match
         '...Australian_Grand_Prix\\Practice_1\\...'
    """
    if df.empty or not session_prefix:
        return df
    prefix = session_prefix.replace(" ", "_")

    # Normalise path separators to forward slashes for consistent matching
    norm_paths = df["_source_file"].str.replace("\\", "/", regex=False)

    # Try direct substring match first
    mask = norm_paths.str.contains(prefix, case=False, na=False)
    if mask.any():
        return df[mask]

    # Folder-path match: split prefix into event + session_type components.
    # Walk known session type suffixes (longest first to avoid partial matches).
    _KNOWN_SESSION_TYPES = [
        "Practice_1", "Practice_2", "Practice_3",
        "Qualifying", "Race",
        "Sprint_Shootout", "Sprint_Qualifying", "Sprint",
    ]
    for st in sorted(_KNOWN_SESSION_TYPES, key=len, reverse=True):
        if prefix.endswith(st):
            event_part = prefix[:-(len(st) + 1)]  # strip "_<session_type>"
            mask = (
                norm_paths.str.contains(event_part, case=False, na=False)
                & norm_paths.str.contains(st, case=False, na=False)
            )
            if mask.any():
                return df[mask]
            break

    # Final fallback: return all data rather than nothing
    return df


class TireDegradationModel:
    """
    Fits a per-compound linear degradation model: LapTime = base + rate * TyreLife.
    Stores coefficients for all compounds.
    """

    def __init__(self):
        self.models: dict = {}       # compound -> {"base": float, "rate": float}
        self.trained_on: list = []
        self.trained_at: str = ""

    def fit(self, df: pd.DataFrame, pre_filtered_race_sim: bool = False) -> None:
        """Fit degradation model from laps DataFrame.

        Args:
            pre_filtered_race_sim: when True the DataFrame has already been
                filtered to race simulation laps by the classifier, so the
                IsAccurate gate is relaxed — we still drop clear outliers
                (out/in laps, lap time < 60 s) but don't discard laps merely
                because IsAccurate is not set.
        """
        df = df.copy()
        df = df[df["LapTimeSec"].notna() & (df["LapTimeSec"] > 60)]
        if "TyreLife" not in df.columns or "Compound" not in df.columns:
            return

        df["TyreLife"] = pd.to_numeric(df["TyreLife"], errors="coerce")
        df = df[df["TyreLife"].notna() & (df["TyreLife"] > 0)]

        # Drop out-laps and in-laps regardless of source.
        if "PitOutTime" in df.columns:
            df = df[df["PitOutTime"].isna()]
        if "PitInTime" in df.columns:
            df = df[df["PitInTime"].isna()]

        # When laps are pre-qualified by the race-sim classifier we skip the
        # IsAccurate gate: the classifier's consistency checks already ensure
        # data quality, and requiring IsAccurate on top would silently discard
        # most valid race sim laps leaving too few to fit per-compound models.
        if not pre_filtered_race_sim and "IsAccurate" in df.columns:
            df = df[df["IsAccurate"].astype(str).str.lower().isin(["true", "1"])]

        # Require tyre age > 1 to exclude warm-up lap even without pit flags
        df = df[df["TyreLife"] > 1]

        # Additionally cap lap times at 115 % of per-compound median to reject
        # safety-car / VSC influenced laps that slipped through.
        if len(df) >= 3 and "Compound" in df.columns:
            compound_medians = df.groupby("Compound")["LapTimeSec"].transform("median")
            df = df[df["LapTimeSec"] <= compound_medians * 1.15]

        for compound, grp in df.groupby("Compound"):
            grp = grp.dropna(subset=["TyreLife", "LapTimeSec"])
            if len(grp) < 3:
                continue
            X = grp["TyreLife"].values
            y = grp["LapTimeSec"].values
            # Simple linear regression
            x_mean = X.mean()
            y_mean = y.mean()
            denom = ((X - x_mean) ** 2).sum()
            if denom == 0:
                rate = 0.0
            else:
                rate = float(((X - x_mean) * (y - y_mean)).sum() / denom)
            base = float(y_mean - rate * x_mean)
            self.models[str(compound)] = {"base": base, "rate": rate}

        self.trained_at = datetime.now().isoformat()

    def predict(self, compound: str, tyre_life: float) -> float:
        """Predict lap time (seconds) for a given compound and tyre age."""
        c = str(compound).upper()
        if c not in self.models:
            # Fallback: use average of all models
            if self.models:
                rates = [m["rate"] for m in self.models.values()]
                bases = [m["base"] for m in self.models.values()]
                return float(np.mean(bases)) + float(np.mean(rates)) * tyre_life
            return float("nan")
        m = self.models[c]
        return m["base"] + m["rate"] * tyre_life

    def degradation_rate(self, compound: str) -> float:
        """Return seconds-per-lap degradation rate for compound."""
        c = str(compound).upper()
        if c in self.models:
            return self.models[c]["rate"]
        return float("nan")

    def summary(self) -> dict:
        return {
            "trained_at": self.trained_at,
            "trained_on": self.trained_on,
            "compounds": {
                c: {
                    "base_lap_time_sec": round(m["base"], 3),
                    "deg_rate_sec_per_lap": round(m["rate"], 4),
                }
                for c, m in self.models.items()
            },
        }


def train_degradation_model(
    session_prefixes=None,
    telemetry_dir: Path = Path("telemetry_out"),
    save_path: Path = MODEL_PATH,
    qual_flags_paths: list = None,
    is_race_session: bool = False,
) -> TireDegradationModel:
    """
    Train a tire degradation model from telemetry lap data.

    Args:
        session_prefixes: list of session prefixes to filter (None = use all)
        telemetry_dir: root of telemetry output
        save_path: where to save the pickled model
        qual_flags_paths: list of paths to qual_flags.csv files; when provided,
            training is restricted to race simulation laps only (improved_label == 'race_sim').
        is_race_session: when True every lap in the session is a race lap — skip
            the qual_flags filter and use all laps directly.
    """
    print(f"[tire_deg] Loading laps from {telemetry_dir}")
    df = _load_laps(telemetry_dir)

    if df.empty:
        print("[tire_deg] No lap data found — model not trained")
        model = TireDegradationModel()
        return model

    if session_prefixes:
        parts = []
        for sp in session_prefixes:
            parts.append(_filter_session(df, sp))
        df = pd.concat(parts, ignore_index=True).drop_duplicates()

    # For Race sessions all laps are race laps — no qual_flags filter needed.
    # Both race sessions and classifier-pre-filtered laps are considered
    # pre_filtered_race_sim so the IsAccurate gate inside fit() is relaxed.
    pre_filtered = False
    if is_race_session:
        print(f"[tire_deg] Race session — all {len(df)} laps treated as race simulation data")
        pre_filtered = True
    elif qual_flags_paths:
        df = _filter_race_sim_laps(df, qual_flags_paths)
        pre_filtered = True  # laps already validated by race-sim classifier
    else:
        print("[tire_deg] No qual_flags provided — training on all laps (not filtered to race_sim)")

    model = TireDegradationModel()
    model.fit(df, pre_filtered_race_sim=pre_filtered)
    model.trained_on = list(session_prefixes or ["all"])

    MODEL_DIR.mkdir(exist_ok=True)
    with open(save_path, "wb") as f:
        pickle.dump(model, f)
    print(f"[tire_deg] Model saved to {save_path}")
    print(f"[tire_deg] Compounds: {list(model.models.keys())}")
    for c, info in model.models.items():
        print(f"  {c}: base={info['base']:.3f}s  rate={info['rate']:.4f}s/lap")
    return model


def predict_live_degradation(
    session: str,
    lookahead: int = 3,
    plot_trigger_flag: bool = False,
    telemetry_dir: Path = Path("telemetry_out"),
    model_path: Path = MODEL_PATH,
    year: int = None,
) -> pd.DataFrame:
    """
    Predict tire degradation for a completed session.

    Args:
        session: session prefix (e.g. 'Australian_Grand_Prix_Practice_1')
        lookahead: number of laps ahead to project
        plot_trigger_flag: if True, generate a PNG
        telemetry_dir: telemetry root
        model_path: path to trained model pickle
        year: year filter (optional)
    """
    # Load or train model
    model = None
    if model_path.exists():
        try:
            with open(model_path, "rb") as f:
                model = pickle.load(f)
            print(f"[tire_deg] Loaded model from {model_path}")
        except Exception as e:
            print(f"[tire_deg] Failed to load model ({e}), retraining...")
            model = None

    if model is None or not model.models:
        print("[tire_deg] Training model on available data...")
        model = train_degradation_model(telemetry_dir=telemetry_dir)

    # Load session laps
    df = _load_laps(telemetry_dir)
    session_df = _filter_session(df, session)
    if session_df.empty:
        print(f"[tire_deg] No data found for session: {session}")
        return pd.DataFrame()

    records = []
    for driver, grp in session_df.groupby("Driver"):
        grp = grp.sort_values("LapNumber") if "LapNumber" in grp.columns else grp
        if "Compound" not in grp.columns or "TyreLife" not in grp.columns:
            continue

        last_row = grp.iloc[-1]
        compound = str(last_row.get("Compound", "UNKNOWN"))
        current_life = float(last_row.get("TyreLife", 1)) if not pd.isna(last_row.get("TyreLife")) else 1.0
        current_lap = float(last_row.get("LapNumber", 0)) if not pd.isna(last_row.get("LapNumber")) else 0.0

        projections = []
        for lap_ahead in range(1, lookahead + 1):
            predicted_life = current_life + lap_ahead
            predicted_time = model.predict(compound, predicted_life)
            projections.append({"lap_ahead": lap_ahead, "predicted_life": predicted_life, "predicted_time": predicted_time})

        deg_rate = model.degradation_rate(compound)
        # Flag pit window: when projected cumulative time loss exceeds 1 second.
        # Use absolute rate to handle edge cases; negative rate means data was insufficient.
        current_time = model.predict(compound, current_life)
        pit_window_lap = None
        if deg_rate > 0:  # only meaningful if tires are actually degrading
            for proj in projections:
                if not np.isnan(proj["predicted_time"]) and not np.isnan(current_time):
                    excess = proj["predicted_time"] - current_time
                    if excess > 1.0:  # >1 second cumulative degradation
                        pit_window_lap = int(current_lap + proj["lap_ahead"])
                        break

        records.append({
            "Driver": driver,
            "Compound": compound,
            "CurrentTyreLife": current_life,
            "DegRate_sec_per_lap": round(deg_rate, 4) if not np.isnan(deg_rate) else None,
            "PredictedLap": pit_window_lap,
            "Projections": json.dumps(projections),
        })

    result_df = pd.DataFrame(records)

    # Save predictions
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_file = telemetry_dir / f"tire_predictions_{session}_{ts}.csv"
    result_df.to_csv(out_file, index=False)
    print(f"[tire_deg] Predictions saved: {out_file}")

    if plot_trigger_flag:
        _plot_degradation(result_df, model, session, telemetry_dir)

    return result_df


def _plot_degradation(result_df: pd.DataFrame, model: TireDegradationModel, session: str, output_dir: Path):
    """Generate a tire degradation rate PNG."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        compounds = list(model.models.keys())
        if not compounds:
            return

        fig, ax = plt.subplots(figsize=(10, 6))
        tyre_ages = np.arange(1, 40)

        colors = {"SOFT": "#E8002D", "MEDIUM": "#FFF200", "HARD": "#EBEBEB",
                  "INTERMEDIATE": "#39B54A", "WET": "#0067FF"}

        for compound in compounds:
            times = [model.predict(compound, age) for age in tyre_ages]
            times = [t for t in times if not np.isnan(t)]
            if times:
                ages_plot = tyre_ages[:len(times)]
                color = colors.get(compound, "#AAAAAA")
                ax.plot(ages_plot, times, label=compound, color=color, linewidth=2)

        ax.set_xlabel("Tyre Age (laps)")
        ax.set_ylabel("Predicted Lap Time (s)")
        ax.set_title(f"Tire Degradation Curves\n{session}")
        ax.legend()
        ax.grid(True, alpha=0.3)

        out_path = output_dir / f"tire_degradation_{session}.png"
        plt.tight_layout()
        plt.savefig(out_path, dpi=150)
        plt.close()
        print(f"[tire_deg] Plot saved: {out_path}")
    except Exception as e:
        print(f"[tire_deg] Plot generation failed: {e}")


def compare_predictions_vs_actual(
    model_path: Path,
    race_laps_df: pd.DataFrame,
    session_label: str,
    output_dir: Path,
) -> Path:
    """
    Generate a comparison chart: FP2/FP3 model predicted degradation curves
    vs actual race lap times per compound.

    Args:
        model_path: path to trained TireDegradationModel pickle (from FP2/FP3)
        race_laps_df: DataFrame with actual race laps (must have LapTime/LapTimeSec,
                      TyreLife, Compound columns)
        session_label: label used in title/filename (e.g. 'Australian_GP_Race')
        output_dir: directory to save the PNG

    Returns:
        Path to the saved PNG, or None if generation failed.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        # Load model
        if not Path(model_path).exists():
            print(f"[tire_deg] Model not found: {model_path}")
            return None
        with open(model_path, "rb") as f:
            model = pickle.load(f)

        if not model.models:
            print("[tire_deg] Model has no compound data — skipping comparison")
            return None

        # Prepare actual race lap data
        df = race_laps_df.copy()
        if "LapTimeSec" not in df.columns and "LapTime" in df.columns:
            df["LapTimeSec"] = df["LapTime"].apply(_parse_laptime_seconds)
        df = df[df["LapTimeSec"].notna() & (df["LapTimeSec"] > 60)]
        if "TyreLife" in df.columns:
            df["TyreLife"] = pd.to_numeric(df["TyreLife"], errors="coerce")
            df = df[df["TyreLife"].notna() & (df["TyreLife"] > 1)]
        if "Compound" in df.columns:
            df["Compound"] = df["Compound"].str.upper()

        colors = {
            "SOFT": "#E8002D", "MEDIUM": "#FFF200", "HARD": "#EBEBEB",
            "INTERMEDIATE": "#39B54A", "WET": "#0067FF",
        }
        marker_styles = {"SOFT": "o", "MEDIUM": "s", "HARD": "^", "INTERMEDIATE": "D", "WET": "v"}

        fig, ax = plt.subplots(figsize=(12, 7))
        fig.patch.set_facecolor("#1a1a2e")
        ax.set_facecolor("#16213e")

        tyre_ages = np.arange(1, 50)

        # Plot predicted model curves
        for compound, m_info in model.models.items():
            compound_upper = compound.upper()
            times = [model.predict(compound, age) for age in tyre_ages]
            times_valid = [(age, t) for age, t in zip(tyre_ages, times) if not np.isnan(t)]
            if not times_valid:
                continue
            ages_plot, times_plot = zip(*times_valid)
            color = colors.get(compound_upper, "#AAAAAA")
            ax.plot(
                ages_plot, times_plot,
                label=f"{compound_upper} (predicted)",
                color=color, linewidth=2.5, linestyle="--", alpha=0.85,
            )

        # Plot actual race lap times as scatter points per compound
        if "Compound" in df.columns and "TyreLife" in df.columns:
            for compound, grp in df.groupby("Compound"):
                compound_upper = compound.upper()
                color = colors.get(compound_upper, "#AAAAAA")
                marker = marker_styles.get(compound_upper, "o")
                ax.scatter(
                    grp["TyreLife"], grp["LapTimeSec"],
                    label=f"{compound_upper} (actual race)",
                    color=color, alpha=0.45, s=18, marker=marker,
                )

        ax.set_xlabel("Tyre Age (laps)", color="white", fontsize=12)
        ax.set_ylabel("Lap Time (s)", color="white", fontsize=12)
        ax.set_title(
            f"Tire Degradation: Predicted (FP2/FP3 model) vs Actual Race\n{session_label}",
            color="white", fontsize=13, pad=14,
        )
        ax.tick_params(colors="white")
        for spine in ax.spines.values():
            spine.set_edgecolor("#444466")
        ax.grid(True, alpha=0.2, color="#8888aa")
        legend = ax.legend(framealpha=0.3, labelcolor="white", fontsize=9)
        legend.get_frame().set_facecolor("#222244")

        # Add annotation: trained on
        trained_on = getattr(model, "trained_on", [])
        if trained_on:
            ax.annotate(
                f"Model trained on: {', '.join(trained_on)}",
                xy=(0.01, 0.02), xycoords="axes fraction",
                color="#aaaacc", fontsize=7.5, alpha=0.8,
            )

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        out_path = output_dir / f"tire_deg_comparison_{session_label}.png"
        plt.tight_layout()
        plt.savefig(out_path, dpi=150, bbox_inches="tight")
        plt.close()
        print(f"[tire_deg] Comparison chart saved: {out_path}")
        return out_path

    except Exception as e:
        print(f"[tire_deg] Comparison chart generation failed: {e}")
        return None


def _cli():
    parser = argparse.ArgumentParser(description="F1 Tire Degradation Model")
    sub = parser.add_subparsers(dest="command")

    train_p = sub.add_parser("train", help="Train model on telemetry data")
    train_p.add_argument("session", nargs="?", default=None, help="Session prefix to train on")
    train_p.add_argument("--year", type=int, default=None)
    train_p.add_argument(
        "--qual-flags", dest="qual_flags", nargs="+", default=None,
        help="Path(s) to qual_flags.csv; restricts training to race_sim laps only",
    )
    train_p.add_argument(
        "--race-session", dest="race_session", action="store_true", default=False,
        help="Treat all laps as race simulation laps (use for Race sessions)",
    )

    pred_p = sub.add_parser("predict", help="Predict degradation for a session")
    pred_p.add_argument("session", help="Session prefix")
    pred_p.add_argument("lookahead", nargs="?", type=int, default=5, help="Laps to project ahead")
    pred_p.add_argument("--year", type=int, default=None)

    cmp_p = sub.add_parser("compare", help="Compare FP2/FP3 model predictions vs actual race data")
    cmp_p.add_argument("race_session", help="Race session prefix (e.g. Australian_Grand_Prix_Race)")
    cmp_p.add_argument("--model", dest="model_path", default=None,
                       help="Path to trained model pkl (defaults to newest FP2/FP3 pkl in models/)")
    cmp_p.add_argument("--output-dir", dest="output_dir", default=None,
                       help="Directory to save comparison PNG (defaults to visualizations/race_session/)")
    cmp_p.add_argument("--year", type=int, default=None)

    args = parser.parse_args()

    if args.command == "train":
        prefixes = [args.session] if args.session else None
        qual_flags_paths = args.qual_flags or None
        model = train_degradation_model(
            session_prefixes=prefixes,
            qual_flags_paths=qual_flags_paths,
            is_race_session=getattr(args, "race_session", False),
        )
        summary = model.summary()
        if summary["compounds"]:
            print("\nModel Summary:")
            for c, info in summary["compounds"].items():
                print(f"  {c}: {info['base_lap_time_sec']:.2f}s base, {info['deg_rate_sec_per_lap']:.4f}s/lap degradation")
        else:
            print("[tire_deg] No compounds modelled (insufficient data)")

        # Save a timestamped copy alongside the main model
        session_label = (args.session or "unknown").replace(" ", "_")
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        ts_path = MODEL_DIR / f"tire_deg_model_{ts}_{session_label}.pkl"
        MODEL_DIR.mkdir(exist_ok=True)
        with open(ts_path, "wb") as f:
            pickle.dump(model, f)
        print(f"[tire_deg] Timestamped model saved: {ts_path}")

    elif args.command == "predict":
        result = predict_live_degradation(
            session=args.session,
            lookahead=args.lookahead or 5,
            year=args.year,
        )
        if not result.empty:
            print(result[["Driver", "Compound", "CurrentTyreLife", "DegRate_sec_per_lap", "PredictedLap"]].to_string(index=False))
        else:
            print("[tire_deg] No predictions generated")

    elif args.command == "compare":
        race_session = args.race_session

        # Resolve model pkl: use --model arg or find newest FP2/FP3 pkl
        model_path = args.model_path
        if not model_path:
            candidates = sorted(
                [p for p in MODEL_DIR.glob("tire_deg_model_*.pkl")
                 if any(x in p.stem.upper() for x in ("FP2", "FP3", "PRACTICE_2", "PRACTICE_3"))],
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if candidates:
                model_path = candidates[0]
                print(f"[tire_deg] Using FP2/FP3 model: {model_path}")
            else:
                model_path = MODEL_PATH
                print(f"[tire_deg] No FP2/FP3 model found — using default: {model_path}")

        # Check if the chosen model has compound data; if not, retrain from all FP2/FP3 data
        _check_model = None
        if Path(model_path).exists():
            try:
                with open(model_path, "rb") as _f:
                    _check_model = pickle.load(_f)
            except Exception:
                pass
        if _check_model is None or not _check_model.models:
            print("[tire_deg] Loaded model has no compound data — retraining from all FP2/FP3 laps")
            # Collect all qual_flags files for FP2/FP3
            fp_qual_flags = sorted(Path("telemetry_out").rglob("qual_flags.csv"))
            fp_prefixes = []
            for qf in fp_qual_flags:
                # session folder is the parent of qual_flags.csv
                session_folder = qf.parent
                event_folder = session_folder.parent
                fp_prefixes.append(f"{event_folder.name}_{session_folder.name}")
            retrained = train_degradation_model(
                session_prefixes=fp_prefixes if fp_prefixes else None,
                qual_flags_paths=[str(p) for p in fp_qual_flags] if fp_qual_flags else None,
                save_path=Path(model_path),
            )
            if not retrained.models:
                # Final fallback: train on all available FP2/FP3 laps without filter
                print("[tire_deg] Race-sim filter still empty — training on all FP2/FP3 laps as fallback")
                retrained = train_degradation_model(
                    session_prefixes=fp_prefixes if fp_prefixes else None,
                    save_path=Path(model_path),
                )
            if retrained.models:
                print(f"[tire_deg] Retrained model compounds: {list(retrained.models.keys())}")

        # Load actual race laps
        race_df = _load_laps(Path("telemetry_out"))
        if not race_df.empty:
            race_df = _filter_session(race_df, race_session)

        # Resolve output dir
        output_dir = args.output_dir
        if not output_dir:
            # Try to derive from session name: visualizations/{year}/{event}/Race
            parts = race_session.rsplit("_", 1)
            if args.year:
                output_dir = Path("visualizations") / str(args.year) / parts[0] / parts[-1]
            else:
                output_dir = Path("visualizations") / race_session

        out_path = compare_predictions_vs_actual(
            model_path=Path(model_path),
            race_laps_df=race_df if not race_df.empty else pd.DataFrame(),
            session_label=race_session,
            output_dir=Path(output_dir),
        )
        if out_path:
            print(f"[tire_deg] Comparison saved: {out_path}")
        else:
            print("[tire_deg] Comparison generation failed or had no data")

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    _cli()
