"""
ers_deployment_map.py
---------------------
Infers ERS deployment zones from telemetry and plots a track map
colored by power mode per driver.

Usage:
    python ers_deployment_map.py

2026 Power Unit architecture:
  - ICE:    ~400 kW (536 hp) — reduced from previous era
  - MGU-K:  ~350 kW (469 hp) — massively enhanced, ~47% of total power
  - MGU-H:  REMOVED — no turbo energy recovery
  - Battery: capped capacity → depletes faster, energy management critical
  - Overtake Mode: replaces DRS — extra electrical energy within 1s of car ahead

Inference logic:
  - ERS_DEPLOY   : Full throttle, RPM below ceiling, good accel
                   → MGU-K deploying alongside ICE (NORMAL full-throttle state)
  - ERS_LAUNCH   : Corner exit, low speed, throttle opening, strong accel
                   → MGU-K burst filling torque gap out of the corner
  - ERS_FADE     : Full throttle, RPM at ceiling, accel well below benchmark
                   → battery depleting, MGU-K contribution dropping
  - ICE_ONLY     : Full throttle, battery empty, ~47% power loss vs ERS+ICE
                   → acceleration substantially lower than benchmark
  - HARVESTING   : Brake > 0, decelerating → MGU-K recovering kinetic energy
  - TRAIL_BRAKE  : Brake + throttle overlap → corner entry clipping
  - ENGINE_BRAKE : Zero pedal, speed falling → passive MGU-K harvest
  - COASTING     : Lift and coast, minimal inputs
  - OVERTAKE     : Overtake Mode active (replaces DRS in 2026)
"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
from pathlib import Path

# ── Config ─────────────────────────────────────────────────────────────────────
YEAR        = 2026
EVENT       = "Australian_Grand_Prix"
SESSION     = "Practice_3"
DRIVERS     = ["RUS", "ANT"]
TELEMETRY_DIR = Path(f"telemetry_out/{YEAR}/{EVENT}/{SESSION}")
OUTPUT_DIR    = Path(f"visualizations/{YEAR}/{EVENT}/{SESSION}")
OUTPUT_FILE   = OUTPUT_DIR / "ers_deployment_map_RUS_ANT.png"

GEAR_COLORS = None  # unused

ZONE_COLORS = {
    "ERS_DEPLOY":    "#00BFFF",   # MGU-K deploying + ICE — normal full-throttle (cyan)
    "ERS_LAUNCH":    "#7B68EE",   # corner exit MGU-K burst (purple)
    "ERS_FADE":      "#FF8C00",   # battery depleting, power falling (orange)
    "ICE_ONLY":      "#00FF88",   # battery empty, ICE alone ~400kW (green)
    "TRAIL_BRAKE":   "#FF69B4",   # brake+throttle overlap — apex clipping (pink)
    "HARVESTING":    "#FF4444",   # MGU-K recovering under braking (red)
    "ENGINE_BRAKE":  "#AA2222",   # passive MGU-K harvest, no pedal (dark red)
    "COASTING":      "#888888",   # lift and coast (gray)
    "OVERTAKE":      "#FFD700",   # Overtake Mode active — extra electrical boost (gold)
    "OTHER":         "#444444",
}

ZONE_LABELS = {
    "ERS_DEPLOY":    "ERS Deploying (MGU-K + ICE)",
    "ERS_LAUNCH":    "ERS Launch (corner exit burst)",
    "ERS_FADE":      "ERS Fading (battery depleting)",
    "ICE_ONLY":      "ICE Only (battery empty)",
    "TRAIL_BRAKE":   "Trail Braking (overlap)",
    "HARVESTING":    "Harvesting (MGU-K braking)",
    "ENGINE_BRAKE":  "Engine Braking (passive harvest)",
    "COASTING":      "Coasting / Lift",
    "OVERTAKE":      "Overtake Mode",
    "OTHER":         "Other",
}

SUMMARY_NAMES = {
    "ERS_DEPLOY":   "ERS straight",
    "ERS_LAUNCH":   "ERS launch",
    "ERS_FADE":     "ERS fade",
    "ICE_ONLY":     "ICE only",
    "HARVESTING":   "Harvest",
    "TRAIL_BRAKE":  "Trail",
    "ENGINE_BRAKE": "Engine",
    "COASTING":     "Coast",
    "OVERTAKE":     "Overtake",
    "OTHER":        "Other",
}

ERS_ZONES = ["ERS_DEPLOY", "ERS_LAUNCH", "ERS_FADE"]

# ── Helpers ────────────────────────────────────────────────────────────────────

def parse_session_time(s):
    try:
        td = pd.to_timedelta(str(s))
        return td.total_seconds()
    except Exception:
        return float("nan")


def find_best_covered_lap(drv: str, laps_df: pd.DataFrame, telem_df: pd.DataFrame):
    """Return the fastest accurate lap fully covered by telemetry."""
    st_sec = telem_df["SessionTime"].apply(parse_session_time)
    telem_min = st_sec.min()
    telem_max = st_sec.max()

    telem_df = telem_df.copy()
    telem_df["_st_sec"] = st_sec

    # Only consider accurate, complete laps
    candidates = laps_df.copy()
    if "IsAccurate" in candidates.columns:
        accurate = candidates["IsAccurate"].astype(str).str.strip().str.lower()
        candidates = candidates[accurate == "true"]

    best_lap = None
    best_time = float("inf")

    for _, row in candidates.iterrows():
        lap_start_sec = parse_session_time(row.get("LapStartTime", None))
        lap_time_sec  = parse_session_time(row.get("LapTime", None))
        if pd.isna(lap_start_sec) or pd.isna(lap_time_sec) or lap_time_sec <= 0:
            continue
        # Only consider sub-2-minute laps (push laps, not race-sim)
        if lap_time_sec > 120:
            continue
        lap_end_sec = lap_start_sec + lap_time_sec
        if lap_start_sec >= telem_min and lap_end_sec <= telem_max + 1:
            window = telem_df[
                (telem_df["_st_sec"] >= lap_start_sec - 0.5) &
                (telem_df["_st_sec"] <= lap_end_sec + 0.5)
            ]
            real_points = (window["Speed"] > 50).sum() if "Speed" in window.columns else 0
            if real_points < 50:
                continue
            if lap_time_sec < best_time:
                best_time = lap_time_sec
                best_lap = int(row["LapNumber"])

    if best_lap is not None:
        m, s = divmod(best_time, 60)
        print(f"  [{drv}] Best covered lap: #{best_lap}  ({int(m)}:{s:06.3f})")
    else:
        print(f"  [{drv}] No accurate push laps found in telemetry range [{telem_min:.0f}s – {telem_max:.0f}s]")
    return best_lap


def load_lap_window(drv: str, lap_number: int, laps_df: pd.DataFrame, telem_df: pd.DataFrame) -> pd.DataFrame:
    """Extract telemetry rows for a specific lap by matching SessionTime window."""
    lap_row = laps_df[laps_df["LapNumber"] == lap_number]
    if lap_row.empty:
        print(f"  [{drv}] Lap {lap_number} not found")
        return pd.DataFrame()

    row = lap_row.iloc[0]
    lap_start_str = row.get("LapStartTime", None)
    lap_time_str  = row.get("LapTime", None)

    lap_start_sec = parse_session_time(lap_start_str)
    lap_time_sec  = parse_session_time(lap_time_str)

    if pd.isna(lap_start_sec) or pd.isna(lap_time_sec):
        print(f"  [{drv}] Cannot parse time window for lap {lap_number}")
        return pd.DataFrame()

    telem_df = telem_df.copy()
    telem_df["_st_sec"] = telem_df["SessionTime"].apply(parse_session_time)

    lap_end_sec = lap_start_sec + lap_time_sec
    window = telem_df[
        (telem_df["_st_sec"] >= lap_start_sec - 0.5) &
        (telem_df["_st_sec"] <= lap_end_sec + 0.5)
    ].copy()

    print(f"  [{drv}] Lap {lap_number}: {lap_time_sec:.3f}s | {len(window)} telemetry points")
    # Drop sentinel/stationary rows — only keep rows where the car is actually moving
    window = window[window["Speed"] > 0]
    on_track = (window["Speed"] > 50).sum()
    print(f"  [{drv}] Lap {lap_number}: {on_track} on-track points (Speed > 50)")
    return window


def estimate_gear_rpm_percentiles(telem_df: pd.DataFrame):
    """For each gear, compute 85th percentile RPM across all laps (proxy for max ICE RPM)."""
    gear_rpm_p85 = {}
    for gear in range(1, 9):
        subset = telem_df[
            (telem_df["nGear"] == gear) &
            (telem_df["Throttle"] > 80) &
            (telem_df["Throttle"] <= 100) &
            (telem_df["Speed"] > 50) &
            (telem_df["RPM"] > 0)
        ]
        if len(subset) > 10:
            gear_rpm_p85[gear] = np.percentile(subset["RPM"].dropna(), 85)
        else:
            gear_rpm_p85[gear] = np.nan
    return gear_rpm_p85


def estimate_gear_accel_benchmarks(telem_df: pd.DataFrame) -> dict:
    """
    For each gear, compute a 'good' full-throttle acceleration benchmark
    (75th percentile dSpeed) used to detect ERS fade.
    Using 75th pct rather than median so the reference represents genuinely
    strong acceleration, making the fade signal clearer.
    """
    benchmarks = {}
    ft = telem_df[
        (telem_df["Throttle"] >= 95) &
        (telem_df["Speed"] > 80)
    ].copy()
    ft = ft[ft["dSpeed"] > 0.2]   # discard noise / flat-spot samples
    for gear in range(1, 9):
        subset = ft[ft["nGear"] == gear]
        if len(subset) > 30:
            benchmarks[gear] = np.percentile(subset["dSpeed"].dropna(), 75)
        else:
            benchmarks[gear] = np.nan
    return benchmarks


def classify_zone(row,
                  gear_rpm_p85: dict,
                  gear_accel_bench: dict | None = None) -> str:
    """
    ERS cycle classification for 2026 50/50 ICE/ERS architecture.

    In 2026, ERS_DEPLOY is the NORMAL full-throttle state (MGU-K always on when
    the battery has charge). ICE_ONLY is the exception — battery empty (~47% power
    loss since MGU-K provides 350kW of the ~750kW total).

    Typical lap cycle:
      HARVESTING / TRAIL_BRAKE  (brake zone — MGU-K recovering)
      → ENGINE_BRAKE / COASTING (corner approach / lift)
      → ERS_LAUNCH              (corner exit, throttle ramping, low speed)
      → ERS_DEPLOY              (straight, MGU-K + ICE, good accel) ← NORMAL
      → ERS_FADE                (battery depleting, accel dropping ~47%)
      → ICE_ONLY                (battery empty, only ~400kW ICE remains)
    """
    throttle  = row.get("Throttle",  0) or 0.0
    brake     = row.get("Brake",     0) or 0.0
    rpm       = row.get("RPM",       0) or 0.0
    gear      = int(row.get("nGear", 0) or 0)
    drs       = row.get("DRS",       0)
    dspeed    = row.get("dSpeed",    0) or 0.0
    speed     = row.get("Speed",     0) or 0.0
    dthrottle = row.get("dThrottle", 0) or 0.0

    # 0) Overtake Mode (replaces DRS in 2026 — extra electrical energy near car ahead)
    try:
        drs_val = int(float(str(drs))) if not pd.isna(drs) else 0
    except Exception:
        drs_val = 0
    if drs_val in (8, 10, 12, 1):
        return "OVERTAKE"

    p85   = gear_rpm_p85.get(gear, np.nan)
    bench = gear_accel_bench.get(gear, np.nan) if gear_accel_bench is not None else np.nan

    # 1) Strong braking harvest — MGU-K recovering kinetic energy
    if brake > 5 and throttle < 25 and dspeed < -0.5:
        return "HARVESTING"

    # 2) Trail braking — brake and throttle overlapping into the apex
    if brake > 5 and throttle >= 25 and dspeed < 0:
        return "TRAIL_BRAKE"

    # 3) Off-throttle decel — engine braking (passive MGU-K harvest) vs lift & coast
    if throttle < 10 and brake <= 1:
        if speed > 120 and dspeed < -1.0:
            return "ENGINE_BRAKE"   # passive MGU-K harvest
        if dspeed <= 0.5:
            return "COASTING"

    # 4) High-throttle deployment regimes
    if throttle >= 80 and brake <= 1:
        if not pd.isna(bench) and bench > 0:
            # ERS launch: corner exit — low speed, throttle opening, strong accel
            if speed < 160 and dthrottle > 5 and dspeed > 0.8 * bench:
                return "ERS_LAUNCH" if (not pd.isna(p85) and rpm < p85) else "ERS_DEPLOY"

            # ICE_ONLY: battery empty — accel is drastically below benchmark
            # (~47% power loss when 350kW MGU-K goes offline)
            if not pd.isna(p85) and rpm > 0.95 * p85 and dspeed < 0.3 * bench:
                return "ICE_ONLY"

            # ERS_FADE: battery depleting — accel noticeably below benchmark
            if not pd.isna(p85) and rpm > 0.95 * p85 and dspeed < 0.5 * bench:
                return "ERS_FADE"

            # ERS_DEPLOY: normal full-throttle state — MGU-K + ICE combined
            # (RPM below ceiling = battery still has charge)
            if not pd.isna(p85) and rpm < p85:
                return "ERS_DEPLOY"

        # No benchmark available — still deploying if RPM below ceiling
        if not pd.isna(p85) and rpm < p85:
            return "ERS_DEPLOY"
        return "ICE_ONLY"

    # 5) Medium throttle — softer ERS push / launch continuation
    if throttle >= 40 and brake <= 1 and dspeed > 0.5:
        return "ERS_LAUNCH" if (not pd.isna(p85) and rpm < p85) else "ICE_ONLY"

    return "OTHER"


def smooth_zones(zones: pd.Series, window: int = 5) -> pd.Series:
    """Majority-vote smoothing to remove single-point noise."""
    result = zones.copy()
    half = window // 2
    for i in range(half, len(zones) - half):
        segment = zones.iloc[i - half: i + half + 1]
        result.iloc[i] = segment.mode().iloc[0]
    return result


def fill_other_gaps(zones: pd.Series, max_run: int = 5) -> pd.Series:
    """Replace short OTHER runs with the surrounding zone for a continuous trace."""
    z = zones.to_numpy().copy()
    n = len(z)
    i = 0
    while i < n:
        if z[i] == "OTHER":
            j = i
            while j < n and z[j] == "OTHER":
                j += 1
            run_len = j - i
            prev_label = z[i - 1] if i > 0 else None
            next_label = z[j] if j < n else None
            if run_len <= max_run and prev_label is not None and prev_label == next_label:
                z[i:j] = prev_label
            i = j
        else:
            i += 1
    return pd.Series(z, index=zones.index)


def fill_all_other(zones: pd.Series) -> pd.Series:
    """Final pass: replace any remaining OTHER via forward/backward fill."""
    s = zones.replace("OTHER", pd.NA)
    s = s.ffill().bfill()
    return s.fillna("OTHER")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    fig, axes = plt.subplots(1, len(DRIVERS), figsize=(9 * len(DRIVERS), 9))
    if len(DRIVERS) == 1:
        axes = [axes]
    fig.patch.set_facecolor("#0d0d1a")

    for ax, drv in zip(axes, DRIVERS):
        laps_path  = TELEMETRY_DIR / f"{EVENT}_{SESSION}_{drv}_laps.csv"
        telem_path = TELEMETRY_DIR / f"{EVENT}_{SESSION}_{drv}_telemetry.csv"

        if not laps_path.exists() or not telem_path.exists():
            print(f"[{drv}] Missing files, skipping")
            ax.set_title(f"{drv} — data missing", color="white")
            continue

        laps_df  = pd.read_csv(laps_path)
        telem_df = pd.read_csv(telem_path)

        laps_df["LapNumber"] = pd.to_numeric(laps_df["LapNumber"], errors="coerce")
        telem_df["Speed"]    = pd.to_numeric(telem_df["Speed"],    errors="coerce")
        telem_df["RPM"]      = pd.to_numeric(telem_df["RPM"],      errors="coerce")
        telem_df["Throttle"] = pd.to_numeric(telem_df["Throttle"], errors="coerce")
        telem_df["Brake"]    = pd.to_numeric(telem_df["Brake"],    errors="coerce")
        telem_df["nGear"]    = pd.to_numeric(telem_df["nGear"],    errors="coerce")
        telem_df["X"]        = pd.to_numeric(telem_df["X"],        errors="coerce")
        telem_df["Y"]        = pd.to_numeric(telem_df["Y"],        errors="coerce")

        # Normalise throttle to 0–100 in case source uses 0–1 scale
        if telem_df["Throttle"].max() <= 1.5:
            telem_df["Throttle"] *= 100

        # Normalise DRS column name (some exports use DRS_Active, DRSOn, etc.)
        if "DRS" not in telem_df.columns:
            drs_cols = [c for c in telem_df.columns if "DRS" in c.upper()]
            if drs_cols:
                telem_df["DRS"] = telem_df[drs_cols[0]]

        # Compute gear RPM percentiles from all telemetry (not just this lap)
        gear_rpm_p85 = estimate_gear_rpm_percentiles(telem_df)
        print(f"[{drv}] Gear RPM 85th pct: { {g: f'{v:.0f}' for g,v in gear_rpm_p85.items() if not pd.isna(v)} }")

        # Auto-select fastest lap covered by telemetry
        lap_num = find_best_covered_lap(drv, laps_df, telem_df)
        if lap_num is None:
            ax.set_title(f"{drv} — no covered laps found", color="white")
            continue

        # Extract best lap telemetry
        lap_telem = load_lap_window(drv, lap_num, laps_df, telem_df)
        if lap_telem.empty:
            ax.set_title(f"{drv} — no telemetry window", color="white")
            continue

        # Drop rows with no position
        lap_telem = lap_telem.dropna(subset=["X", "Y"])
        if len(lap_telem) < 10:
            ax.set_title(f"{drv} — insufficient positional data", color="white")
            continue

        # Pre-compute speed delta and throttle rate for classifier
        lap_telem = lap_telem.copy()
        lap_telem["dSpeed"]    = lap_telem["Speed"].diff().fillna(0)
        lap_telem["dThrottle"] = lap_telem["Throttle"].diff().fillna(0)

        # Compute acceleration benchmarks from full-session telemetry (needs dSpeed)
        telem_df["dSpeed"] = telem_df["Speed"].diff().fillna(0)
        gear_accel_bench = estimate_gear_accel_benchmarks(telem_df)
        print(f"[{drv}] Gear accel bench (km/h/sample): "
              f"{ {g: f'{v:.2f}' for g, v in gear_accel_bench.items() if not pd.isna(v)} }")

        # Classify each point
        lap_telem["zone"] = lap_telem.apply(
            lambda r: classify_zone(r, gear_rpm_p85, gear_accel_bench), axis=1)
        lap_telem["zone"] = smooth_zones(lap_telem["zone"])
        lap_telem["zone"] = fill_other_gaps(lap_telem["zone"], max_run=10)

        # ── Draw track outline (gray background) ──
        ax.set_facecolor("#0d0d1a")
        ax.plot(lap_telem["X"], lap_telem["Y"], color="#333355", linewidth=6, zorder=1)

        # ── Color segments by zone ──
        zone_order = [
            "OVERTAKE",
            "ERS_DEPLOY",
            "ERS_LAUNCH",
            "ERS_FADE",
            "ICE_ONLY",
            "HARVESTING",
            "TRAIL_BRAKE",
            "ENGINE_BRAKE",
            "COASTING",
        ]
        drawn_zones = set()

        xs = lap_telem["X"].values
        ys = lap_telem["Y"].values
        zones = lap_telem["zone"].values

        for i in range(len(xs) - 1):
            z = zones[i]
            color = ZONE_COLORS.get(z, "#444444")
            label = ZONE_LABELS.get(z, z) if z not in drawn_zones else None
            ax.plot([xs[i], xs[i+1]], [ys[i], ys[i+1]],
                    color=color, linewidth=3, solid_capstyle="round",
                    label=label, zorder=2)
            drawn_zones.add(z)

        # ── Lap time from laps_df ──
        lap_row = laps_df[laps_df["LapNumber"] == lap_num]
        lap_time_str = "—"
        if not lap_row.empty:
            lt = lap_row.iloc[0].get("LapTime", "")
            try:
                secs = pd.to_timedelta(str(lt)).total_seconds()
                m, s = divmod(secs, 60)
                lap_time_str = f"{int(m)}:{s:06.3f}"
            except Exception:
                lap_time_str = str(lt)

        # Zone % breakdown
        zone_counts = lap_telem["zone"].value_counts(normalize=True) * 100
        ers_total = sum(zone_counts.get(z, 0) for z in ERS_ZONES)
        summary = f"ERS total: {ers_total:.0f}%   " + "  ".join(
            f"{SUMMARY_NAMES[z]}: {zone_counts.get(z, 0):.0f}%"
            for z in zone_order if zone_counts.get(z, 0) > 1
        )

        ax.set_title(f"{drv}  —  {lap_time_str}  (Lap {lap_num})\n{summary}",
                     color="white", fontsize=11, pad=10)
        ax.set_aspect("equal")
        ax.axis("off")

        # Legend (only for first driver, or deduplicate)
        handles, labels = ax.get_legend_handles_labels()
        by_label = dict(zip(labels, handles))
        ax.legend(by_label.values(), by_label.keys(),
                  loc="lower left", fontsize=8,
                  facecolor="#1a1a2e", labelcolor="white", framealpha=0.8)

    fig.suptitle(
        f"ERS Deployment Map — {EVENT.replace('_',' ')} {SESSION.replace('_',' ')}\n"
        f"Mercedes AMG: RUS vs ANT — Best Push Laps",
        color="white", fontsize=14, fontweight="bold", y=0.98
    )

    # Shared legend explanation
    fig.text(0.5, 0.01,
             "Cyan = ERS deploy (normal)  |  Purple = ERS launch  |  Orange = ERS fading  |  "
             "Green = ICE only (empty)  |  Pink = Trail brake  |  Red = Harvesting  |  "
             "Dark red = Engine brake  |  Gray = Coast  |  Gold = Overtake Mode",
             ha="center", color="#aaaacc", fontsize=8)

    plt.tight_layout(rect=[0, 0.03, 1, 0.97])
    plt.savefig(OUTPUT_FILE, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    print(f"\nSaved: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
