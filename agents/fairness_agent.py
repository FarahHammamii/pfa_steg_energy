import pandas as pd
import numpy as np
from typing import Dict, Any, List, Optional
from agents.base_agent import BaseAgent
from agents.data_loader import DataLoader
from utils.db import get_cursor
from utils.logger import get_logger
import os

# Groq setup
try:
    from groq import Groq
    GROQ_AVAILABLE = True
except ImportError:
    GROQ_AVAILABLE = False

logger = get_logger(__name__)

groq_key = os.getenv("GROQ_API_KEY")
if GROQ_AVAILABLE and groq_key:
    client = Groq(api_key=groq_key)
    GROQ_MODEL = "llama-3.3-70b-versatile"
    logger.info("Groq client ready for Fairness Agent")
else:
    client = None

# ── Thresholds ────────────────────────────────────────────────────────────────
GINI_THRESHOLD = 0.4
GINI_TARGET    = 0.3
SUMMER_MONTHS  = {6, 7, 8, 9}
WINTER_MONTHS  = {12, 1, 2, 3}

# ── Traffic light bands ───────────────────────────────────────────────────────
# (max_gini, color, label)
TRAFFIC_LIGHT = [
    (0.20, "green",  "Very Fair"),
    (0.30, "green",  "Fair"),
    (0.40, "amber",  "Borderline"),
    (0.60, "red",    "Unfair"),
    (1.00, "red",    "Very Unfair"),
]

# ── Severity cut weights ──────────────────────────────────────────────────────
# Mirrors the tier system from Cut Advisor
CUT_WEIGHTS = {
    "cut_immediately": 1.0,
    "cut_if_worsens":  0.5,
    "protected":       0.0,
}


class FairnessAgent(BaseAgent):
    """Ensures cuts are distributed fairly across regions."""

    def __init__(self):
        super().__init__("Fairness Agent")
        self.data_loader = DataLoader()

    # ─────────────────────────────────────────────────────────────────────────
    # Public entry points
    # ─────────────────────────────────────────────────────────────────────────

    def analyze(self) -> Dict[str, Any]:
        """Full monthly fairness analysis — main entry point."""

        cut_history     = self._load_cut_history()
        consumption     = self._load_consumption()
        population      = self._load_population()

        logger.info(f"Loaded cut history: {len(cut_history)} records")

        if len(cut_history) == 0:
            return {
                'status':            'no_data',
                'message':           'No cut history available. Run synthetic data generator first.',
                'fairness_score':    0,
                'traffic_light':     self._traffic_light(0.0),
                'most_cut_regions':  [],
                'least_cut_regions': [],
                'unfair_regions':    [],
                'recommendations':   ['Generate synthetic cut history to enable fairness analysis'],
                'needs_rebalancing': False,
            }

        # ── Core metrics ─────────────────────────────────────────────────────
        fairness_metrics  = self._calculate_fairness_metrics(cut_history, consumption)
        seasonal_metrics  = self._calculate_seasonal_gini(cut_history)
        monthly_trend     = self._calculate_monthly_gini_trend(cut_history)
        trend_direction   = self._calculate_trend_direction(monthly_trend)
        unfair_regions    = self._identify_unfair_regions(cut_history)
        double_penalty    = self._detect_double_penalty(cut_history)
        per_capita        = self._calculate_per_capita_burden(cut_history, population)

        # ── Rotation schedule ─────────────────────────────────────────────────
        rotation = {}
        if fairness_metrics['gini_coefficient'] > GINI_THRESHOLD:
            cut_counts = (
                cut_history.groupby('region')
                .size()
                .reset_index(name='cut_count')
            )
            rotation = self._generate_rotation_schedule(cut_counts)

        recommendations = self._generate_fairness_recommendations(
            unfair_regions, fairness_metrics, double_penalty,
            trend_direction, per_capita
        )

        # ── LLM explanation ───────────────────────────────────────────────────
        if client and (unfair_regions or double_penalty):
            explanation = self._get_llm_explanation(
                fairness_metrics, seasonal_metrics, trend_direction,
                unfair_regions, double_penalty, per_capita, recommendations
            )
        else:
            explanation = self._get_fallback_explanation(
                fairness_metrics, seasonal_metrics, trend_direction
            )

        return {
            'status':                  'success',
            'fairness_score':          fairness_metrics['gini_coefficient'],
            'fairness_score_weighted': fairness_metrics['gini_weighted'],
            'fairness_score_duration': fairness_metrics['gini_by_duration'],
            'traffic_light':           self._traffic_light(fairness_metrics['gini_coefficient']),
            'seasonal_gini':           seasonal_metrics,
            'monthly_trend':           monthly_trend,
            'trend_direction':         trend_direction,
            'most_cut_regions':        fairness_metrics['most_cut'],
            'least_cut_regions':       fairness_metrics['least_cut'],
            'unfair_regions':          unfair_regions,
            'double_penalty':          double_penalty,
            'per_capita_burden':       per_capita,
            'rotation_schedule':       rotation,
            'recommendations':         recommendations,
            'needs_rebalancing':       fairness_metrics['gini_coefficient'] > GINI_THRESHOLD,
            'total_cuts_analyzed':     fairness_metrics['total_cuts'],
            'explanation':             explanation,
        }

    def validate_cut_list(self, proposed_regions: List[str]) -> Dict[str, Any]:
        """
        Cross-validation gate — called by Cut Advisor BEFORE finalising cuts.
        Simulates adding proposed cuts and checks if Gini would exceed threshold.
        """
        cut_history  = self._load_cut_history()
        consumption  = self._load_consumption()

        if len(cut_history) == 0:
            return {
                'approved':     True,
                'gini_before':  0.0,
                'gini_after':   0.0,
                'substitution': None,
                'message':      'No cut history — approved by default.'
            }

        cut_counts = (
            cut_history.groupby('region')
            .size()
            .reset_index(name='cut_count')
            .set_index('region')['cut_count']
            .to_dict()
        )

        weights      = self._build_consumption_weights(consumption)
        gini_before  = self._gini_weighted(cut_counts, weights)

        simulated = dict(cut_counts)
        for region in proposed_regions:
            simulated[region] = simulated.get(region, 0) + 1

        gini_after = self._gini_weighted(simulated, weights)

        if gini_after <= GINI_THRESHOLD:
            return {
                'approved':     True,
                'gini_before':  round(gini_before, 4),
                'gini_after':   round(gini_after, 4),
                'substitution': None,
                'message':      f'Approved. Gini stays at {round(gini_after, 4)} (≤ {GINI_THRESHOLD}).'
            }

        candidate_pool = set(cut_counts.keys()) - set(proposed_regions)
        substitution   = (
            min(candidate_pool, key=lambda r: cut_counts.get(r, 0))
            if candidate_pool else None
        )

        return {
            'approved':     False,
            'gini_before':  round(gini_before, 4),
            'gini_after':   round(gini_after, 4),
            'substitution': substitution,
            'message': (
                f'REJECTED: cuts would raise Gini {round(gini_before,4)} → '
                f'{round(gini_after,4)} (threshold {GINI_THRESHOLD}). '
                + (f"Suggest swapping a region for '{substitution}'." if substitution else '')
            )
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Data loading
    # ─────────────────────────────────────────────────────────────────────────

    def _load_cut_history(self) -> pd.DataFrame:
        """Load cut history from silver — includes severity tier if available."""
        query = """
            SELECT
                region,
                cut_date,
                duration_minutes,
                season,
                severity_tier  -- 'cut_immediately' | 'cut_if_worsens' | 'protected'
            FROM silver.cut_history
            ORDER BY cut_date DESC
        """
        try:
            with get_cursor(dict_cursor=True) as cur:
                cur.execute(query)
                rows = cur.fetchall()
                return pd.DataFrame(rows) if rows else pd.DataFrame()
        except Exception:
            # severity_tier column might not exist yet — fall back gracefully
            query_fallback = """
                SELECT region, cut_date, duration_minutes, season
                FROM silver.cut_history
                ORDER BY cut_date DESC
            """
            with get_cursor(dict_cursor=True) as cur:
                cur.execute(query_fallback)
                rows = cur.fetchall()
                df = pd.DataFrame(rows) if rows else pd.DataFrame()
                if len(df) > 0:
                    df['severity_tier'] = 'cut_immediately'
                return df

    def _load_consumption(self) -> pd.DataFrame:
        """Load regional consumption shares for weighted Gini."""
        query = """
            SELECT
                gouvernorat                         AS region,
                AVG(share_of_national_pct)          AS share_pct,
                AVG(estimated_consumption_gwh)      AS consumption_gwh
            FROM gold.regional_consumption
            GROUP BY gouvernorat
        """
        with get_cursor(dict_cursor=True) as cur:
            cur.execute(query)
            rows = cur.fetchall()
            return pd.DataFrame(rows) if rows else pd.DataFrame()

    def _load_solar_investment(self) -> pd.DataFrame:
        """Load total investment per region from gold.solar_impact."""
        query = """
            SELECT region, SUM(total_investment_dt) AS total_investment
            FROM gold.solar_impact
            GROUP BY region
        """
        with get_cursor(dict_cursor=True) as cur:
            cur.execute(query)
            rows = cur.fetchall()
            return pd.DataFrame(rows) if rows else pd.DataFrame()

    def _load_population(self) -> pd.DataFrame:
        """
        Load population per region per year.
        Falls back gracefully if table doesn't exist yet.
        """
        query = """
            SELECT gouvernorat AS region, year, population, is_estimated
            FROM gold.gouvernorat_population
        """
        try:
            with get_cursor(dict_cursor=True) as cur:
                cur.execute(query)
                rows = cur.fetchall()
                return pd.DataFrame(rows) if rows else pd.DataFrame()
        except Exception:
            logger.warning("gold.gouvernorat_population not found — per-capita metrics skipped.")
            return pd.DataFrame()

    # ─────────────────────────────────────────────────────────────────────────
    # Gini math
    # ─────────────────────────────────────────────────────────────────────────

    def _gini_from_counts(self, values: List[float]) -> float:
        """Standard unweighted Gini coefficient."""
        if not values or sum(values) == 0:
            return 0.0
        arr = np.sort(np.array(values, dtype=float))
        n   = len(arr)
        idx = np.arange(1, n + 1)
        return float(
            (2 * np.sum(idx * arr) - (n + 1) * np.sum(arr)) / (n * np.sum(arr))
        )

    def _gini_from_duration(self, df: pd.DataFrame) -> float:
        """Duration-weighted Gini — total cut hours per region."""
        if len(df) == 0:
            return 0.0
        duration_by_region = df.groupby('region')['duration_minutes'].sum()
        return self._gini_from_counts(duration_by_region.tolist())

    def _build_consumption_weights(self, consumption: pd.DataFrame) -> Dict[str, float]:
        """
        Returns {region: share_of_national_pct} normalised to sum to 1.
        Falls back to equal weights if no consumption data.
        """
        if len(consumption) == 0:
            return {}
        return (
            consumption.set_index('region')['share_pct']
            .to_dict()
        )

    def _gini_weighted(
        self,
        cut_counts: Dict[str, float],
        weights:    Dict[str, float],
    ) -> float:
        """
        Consumption-weighted Gini.

        Regions with higher consumption share get more weight —
        cutting Sfax (9% national share) counts more than cutting
        Kebili (1.6%) in the fairness calculation.

        If no weights provided, falls back to unweighted Gini.
        """
        if not weights:
            return self._gini_from_counts(list(cut_counts.values()))

        regions = list(cut_counts.keys())
        if not regions:
            return 0.0

        total_weight = sum(weights.get(r, 1.0) for r in regions)

        # burden = cuts / consumption_share — how hard each unit of consumption is hit
        burden = [
            cut_counts[r] / max(weights.get(r, 1.0) / total_weight, 0.001)
            for r in regions
        ]
        return self._gini_from_counts(burden)

    def _severity_weighted_counts(self, cut_history: pd.DataFrame) -> Dict[str, float]:
        """
        Weight each cut by its severity tier.
        cut_immediately = 1.0, cut_if_worsens = 0.5, protected = 0.0
        """
        if 'severity_tier' not in cut_history.columns:
            return cut_history.groupby('region').size().to_dict()

        cut_history = cut_history.copy()
        cut_history['weight'] = cut_history['severity_tier'].map(
            lambda t: CUT_WEIGHTS.get(t, 1.0)
        )
        return (
            cut_history.groupby('region')['weight']
            .sum()
            .to_dict()
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Traffic light
    # ─────────────────────────────────────────────────────────────────────────

    def _traffic_light(self, gini: float) -> Dict[str, str]:
        """
        Human-readable status band for the Superset dashboard.
        Returns color and label so operators don't need to know what 0.4 means.
        """
        for max_g, color, label in TRAFFIC_LIGHT:
            if gini <= max_g:
                return {'color': color, 'label': label, 'gini': round(gini, 3)}
        return {'color': 'red', 'label': 'Very Unfair', 'gini': round(gini, 3)}

    # ─────────────────────────────────────────────────────────────────────────
    # Core fairness metrics
    # ─────────────────────────────────────────────────────────────────────────

    def _calculate_fairness_metrics(
        self,
        cut_history: pd.DataFrame,
        consumption: pd.DataFrame,
    ) -> Dict[str, Any]:
        """Overall Gini by count, duration, severity weight, and consumption weight."""

        if len(cut_history) == 0:
            return {
                'gini_coefficient': 0, 'gini_weighted': 0,
                'gini_by_duration': 0, 'most_cut': [],
                'least_cut': [], 'total_cuts': 0,
            }

        cut_counts = (
            cut_history.groupby('region')
            .agg(cut_count=('region', 'size'), total_minutes=('duration_minutes', 'sum'))
            .reset_index()
        )

        weights           = self._build_consumption_weights(consumption)
        severity_counts   = self._severity_weighted_counts(cut_history)

        gini_count    = self._gini_from_counts(cut_counts['cut_count'].tolist())
        gini_duration = self._gini_from_duration(cut_history)
        gini_weighted = self._gini_weighted(severity_counts, weights)

        most_cut  = cut_counts.nlargest(3,  'cut_count')[['region', 'cut_count']].to_dict('records')
        least_cut = cut_counts.nsmallest(3, 'cut_count')[['region', 'cut_count']].to_dict('records')

        return {
            'gini_coefficient': round(gini_count, 3),
            'gini_by_duration': round(gini_duration, 3),
            'gini_weighted':    round(gini_weighted, 3),
            'most_cut':         most_cut,
            'least_cut':        least_cut,
            'total_cuts':       len(cut_history),
        }

    def _calculate_seasonal_gini(self, cut_history: pd.DataFrame) -> Dict[str, Any]:
        """
        Separate Gini for summer vs winter.
        A region cut more in summer may look fair annually
        but is penalised during peak stress periods.
        """
        if len(cut_history) == 0 or 'season' not in cut_history.columns:
            return {'summer': 0.0, 'winter': 0.0, 'seasonal_flags': []}

        summer = cut_history[cut_history['season'].str.lower() == 'summer']
        winter = cut_history[cut_history['season'].str.lower() == 'winter']

        gini_summer = self._gini_from_duration(summer) if len(summer) > 1 else 0.0
        gini_winter = self._gini_from_duration(winter) if len(winter) > 1 else 0.0

        seasonal_flags = []
        if len(summer) > 0 and len(winter) > 0:
            summer_counts = summer.groupby('region').size()
            winter_counts = winter.groupby('region').size()
            for region in summer_counts.index:
                s = summer_counts.get(region, 0)
                w = winter_counts.get(region, 0)
                if s > 0 and w == 0:
                    seasonal_flags.append(f'{region}: summer only')
                elif s > w * 2:
                    seasonal_flags.append(f'{region}: {s} summer vs {w} winter cuts')

        return {
            'summer':         round(gini_summer, 3),
            'winter':         round(gini_winter, 3),
            'seasonal_flags': seasonal_flags,
        }

    def _calculate_monthly_gini_trend(self, cut_history: pd.DataFrame) -> List[Dict]:
        """Cumulative Gini month by month."""
        if len(cut_history) == 0:
            return []

        df = cut_history.copy()
        df['cut_date'] = pd.to_datetime(df['cut_date'])
        df['month_key'] = df['cut_date'].dt.to_period('M')

        cumulative: Dict[str, int] = {}
        trend = []

        for period in sorted(df['month_key'].unique()):
            month_df = df[df['month_key'] == period]
            for region, count in month_df.groupby('region').size().items():
                cumulative[region] = cumulative.get(region, 0) + count

            gini = self._gini_from_counts(list(cumulative.values()))
            trend.append({
                'year':  period.year,
                'month': period.month,
                'gini':  round(gini, 4),
            })

        return trend

    def _calculate_trend_direction(self, monthly_trend: List[Dict]) -> Dict[str, Any]:
        """
        Linear regression slope on the last 6 months of Gini scores.
        Tells you: is fairness getting better or worse?
        """
        if len(monthly_trend) < 3:
            return {'direction': 'insufficient_data', 'slope': 0.0, 'summary': 'Not enough data yet.'}

        recent = monthly_trend[-6:]
        x = np.arange(len(recent), dtype=float)
        y = np.array([p['gini'] for p in recent], dtype=float)

        slope = float(np.polyfit(x, y, 1)[0])

        if slope < -0.005:
            direction = 'improving'
            summary   = f'Fairness is improving (Gini falling by ~{abs(round(slope, 4))} per month).'
        elif slope > 0.005:
            direction = 'worsening'
            summary   = f'Fairness is worsening (Gini rising by ~{round(slope, 4)} per month).'
        else:
            direction = 'stable'
            summary   = 'Fairness is stable with no significant trend.'

        return {
            'direction': direction,
            'slope':     round(slope, 6),
            'summary':   summary,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Unfair region detection
    # ─────────────────────────────────────────────────────────────────────────

    def _identify_unfair_regions(self, cut_history: pd.DataFrame) -> List[Dict]:
        """Regions more than 1 std dev above or below average cut count."""
        if len(cut_history) == 0:
            return []

        cut_counts = cut_history.groupby('region').size().reset_index(name='cut_count')
        avg = cut_counts['cut_count'].mean()
        std = cut_counts['cut_count'].std()

        unfair = []
        for _, row in cut_counts.iterrows():
            if row['cut_count'] > avg + std:
                unfair.append({
                    'region':           row['region'],
                    'issue':            'cut_too_often',
                    'cut_count':        int(row['cut_count']),
                    'above_average_by': int(row['cut_count'] - avg),
                })
            elif row['cut_count'] < avg - std:
                unfair.append({
                    'region':           row['region'],
                    'issue':            'cut_too_rarely',
                    'cut_count':        int(row['cut_count']),
                    'below_average_by': int(avg - row['cut_count']),
                })

        return unfair

    def _detect_double_penalty(self, cut_history: pd.DataFrame) -> List[Dict]:
        """
        Regions with BOTH below-average solar investment AND above-average cuts.
        Already economically disadvantaged AND being cut more.
        """
        investment_df = self._load_solar_investment()

        if len(investment_df) == 0 or len(cut_history) == 0:
            return []

        cut_counts = cut_history.groupby('region').size().reset_index(name='cut_count')
        merged     = cut_counts.merge(investment_df, on='region', how='inner')

        if len(merged) == 0:
            logger.warning(
                "Double-penalty merge returned 0 rows — region names likely "
                "don't match between silver.cut_history and gold.solar_impact. "
                "Fix with gold.dim_gouvernorat once ready."
            )
            return []

        avg_inv  = merged['total_investment'].mean()
        avg_cuts = merged['cut_count'].mean()

        flagged = []
        for _, row in merged.iterrows():
            if row['total_investment'] < avg_inv and row['cut_count'] > avg_cuts:
                flagged.append({
                    'region':              row['region'],
                    'cut_count':           int(row['cut_count']),
                    'total_investment_dt': round(float(row['total_investment']), 2),
                    'investment_gap_pct':  round((avg_inv - row['total_investment']) / avg_inv * 100, 1),
                    'cut_excess_pct':      round((row['cut_count'] - avg_cuts) / avg_cuts * 100, 1),
                    'severity':            'high' if (
                        row['total_investment'] < avg_inv * 0.5
                        and row['cut_count'] > avg_cuts * 1.5
                    ) else 'medium',
                })

        return sorted(flagged, key=lambda x: x['cut_count'], reverse=True)

    # ─────────────────────────────────────────────────────────────────────────
    # Per-capita burden
    # ─────────────────────────────────────────────────────────────────────────

    def _calculate_per_capita_burden(
        self,
        cut_history:  pd.DataFrame,
        population:   pd.DataFrame,
    ) -> List[Dict]:
        """
        Cuts per 1000 residents per region.
        Most human-readable fairness metric for the jury.
        Skipped gracefully if population table not yet available.
        """
        if len(population) == 0 or len(cut_history) == 0:
            return []

        # Use most recent year available
        latest_year = population['year'].max()
        pop_latest  = population[population['year'] == latest_year][['region', 'population']]

        cut_counts  = cut_history.groupby('region').size().reset_index(name='cut_count')
        merged      = cut_counts.merge(pop_latest, on='region', how='inner')

        if len(merged) == 0:
            return []

        merged['cuts_per_1000'] = (
            merged['cut_count'] / merged['population'] * 1000
        ).round(2)

        avg_per_capita = merged['cuts_per_1000'].mean()

        result = []
        for _, row in merged.sort_values('cuts_per_1000', ascending=False).iterrows():
            result.append({
                'region':         row['region'],
                'cut_count':      int(row['cut_count']),
                'population':     int(row['population']),
                'cuts_per_1000':  float(row['cuts_per_1000']),
                'vs_average':     round(
                    (row['cuts_per_1000'] - avg_per_capita) / max(avg_per_capita, 0.001) * 100, 1
                ),
            })

        return result

    # ─────────────────────────────────────────────────────────────────────────
    # Rotation schedule
    # ─────────────────────────────────────────────────────────────────────────

    def _generate_rotation_schedule(
        self,
        cut_counts:   pd.DataFrame,
        cuts_needed:  int = 8,
    ) -> Dict[str, Any]:
        """
        4-week calendar that brings Gini back under GINI_TARGET (0.3).
        Least-cut regions are prioritised. No region appears twice in
        the same week.
        """
        if len(cut_counts) == 0:
            return {}

        sorted_regions = cut_counts.sort_values('cut_count')['region'].tolist()
        n              = len(sorted_regions)
        cuts_per_week  = max(1, cuts_needed // 4)

        schedule: Dict[str, List[str]] = {}
        used_this_week: set            = set()
        idx = 0

        for week in range(1, 5):
            used_this_week = set()
            week_regions   = []
            attempts       = 0

            while len(week_regions) < cuts_per_week and attempts < n * 2:
                candidate = sorted_regions[idx % n]
                idx      += 1
                attempts += 1
                if candidate not in used_this_week:
                    week_regions.append(candidate)
                    used_this_week.add(candidate)

            schedule[f'week_{week}'] = week_regions

        # Project Gini after rotation
        projected = cut_counts.set_index('region')['cut_count'].to_dict()
        for regions_in_week in schedule.values():
            for r in regions_in_week:
                projected[r] = projected.get(r, 0) + 1

        projected_gini = self._gini_from_counts(list(projected.values()))

        return {
            'schedule':       schedule,
            'cuts_planned':   cuts_needed,
            'projected_gini': round(projected_gini, 4),
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Recommendations
    # ─────────────────────────────────────────────────────────────────────────

    def _generate_fairness_recommendations(
        self,
        unfair_regions:  List[Dict],
        metrics:         Dict,
        double_penalty:  List[Dict],
        trend_direction: Dict,
        per_capita:      List[Dict],
    ) -> List[str]:

        recommendations = []

        # Trend direction
        if trend_direction['direction'] == 'worsening':
            recommendations.append(
                f"Warning: {trend_direction['summary']} Immediate rebalancing recommended."
            )

        # Unfair regions
        for region in unfair_regions:
            if region['issue'] == 'cut_too_often':
                recommendations.append(
                    f"Reduce cut frequency in {region['region']} — "
                    f"cut {region['cut_count']} times, {region['above_average_by']} above average."
                )
            else:
                recommendations.append(
                    f"Increase cut priority for {region['region']} — "
                    f"only cut {region['cut_count']} times."
                )

        # Double-penalty
        for region in double_penalty:
            recommendations.append(
                f"Double-penalty alert: {region['region']} has "
                f"{region['investment_gap_pct']}% below-average solar investment "
                f"AND {region['cut_excess_pct']}% more cuts than average "
                f"(severity: {region['severity']})."
            )

        # Weighted vs unweighted gap
        if abs(metrics.get('gini_weighted', 0) - metrics.get('gini_coefficient', 0)) > 0.1:
            recommendations.append(
                "Consumption-weighted Gini differs significantly from raw count Gini — "
                "high-consumption regions are carrying a disproportionate burden."
            )

        # Duration gap
        if metrics.get('gini_by_duration', 0) > metrics.get('gini_coefficient', 0) + 0.1:
            recommendations.append(
                "Duration-weighted Gini is significantly higher than count-based Gini — "
                "some regions experience longer cuts, not just more frequent ones."
            )

        # Per-capita top offender
        if per_capita:
            worst = per_capita[0]
            best  = per_capita[-1]
            if worst['cuts_per_1000'] > best['cuts_per_1000'] * 2:
                recommendations.append(
                    f"{worst['region']} residents experience {worst['cuts_per_1000']} cuts "
                    f"per 1000 people vs {best['cuts_per_1000']} in {best['region']} — "
                    f"a {round(worst['cuts_per_1000'] / max(best['cuts_per_1000'], 0.01), 1)}x gap."
                )

        # Global threshold
        if metrics['gini_coefficient'] > GINI_THRESHOLD:
            recommendations.append(
                "High inequality detected — implement rotating cut schedule across all regions."
            )

        if not recommendations:
            recommendations.append("Cut distribution is fair. No adjustments needed.")

        return recommendations

    # ─────────────────────────────────────────────────────────────────────────
    # LLM explanation
    # ─────────────────────────────────────────────────────────────────────────

    def _get_llm_explanation(
        self,
        metrics:         Dict,
        seasonal:        Dict,
        trend_direction: Dict,
        unfair_regions:  List[Dict],
        double_penalty:  List[Dict],
        per_capita:      List[Dict],
        recommendations: List[str],
    ) -> str:

        top_per_capita = (
            f"{per_capita[0]['region']}: {per_capita[0]['cuts_per_1000']} cuts/1000 residents"
            if per_capita else 'N/A'
        )

        context = f"""
Fairness Metrics:
- Gini (count-based):    {metrics['gini_coefficient']}
- Gini (duration-based): {metrics.get('gini_by_duration', 'N/A')}
- Gini (weighted):       {metrics.get('gini_weighted', 'N/A')}
- Traffic light status:  {self._traffic_light(metrics['gini_coefficient'])['label']}
- Total cuts analyzed:   {metrics['total_cuts']}
- Most cut region:       {metrics['most_cut'][0]['region'] if metrics['most_cut'] else 'N/A'}
- Least cut region:      {metrics['least_cut'][0]['region'] if metrics['least_cut'] else 'N/A'}

Trend:
- Direction: {trend_direction['direction']}
- Summary:   {trend_direction['summary']}

Seasonal Gini:
- Summer: {seasonal.get('summer', 'N/A')}
- Winter: {seasonal.get('winter', 'N/A')}
- Seasonal flags: {', '.join(seasonal.get('seasonal_flags', [])) or 'None'}

Per-capita burden (top region):
- {top_per_capita}

Unfair Regions:
{', '.join([f"{r['region']} ({r['issue']})" for r in unfair_regions]) or 'None'}

Double-Penalty Regions:
{', '.join([f"{r['region']} severity={r['severity']}" for r in double_penalty]) or 'None'}

Recommendations:
{chr(10).join(recommendations)}
"""

        prompt = f"""
You are STEG's fairness auditor for electricity grid management in Tunisia.

INSTRUCTIONS:
- Answer ONLY using the provided context.
- DO NOT use your prior knowledge.
- Explain what the fairness metrics mean in plain language.
- Highlight trend direction, seasonal unfairness, and double-penalty regions.
- Keep the explanation to 3-4 sentences.

==================== CONTEXT ====================
{context}

==================== QUESTION ====================
Is the current distribution of power cuts fair across regions?
What does the data tell us, and are any regions being disproportionately affected?

==================== ANSWER ====================
"""

        try:
            response = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=512,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"Groq API error: {e}")
            return self._get_fallback_explanation(metrics, seasonal, trend_direction)

    def _get_fallback_explanation(
        self,
        metrics:         Dict,
        seasonal:        Dict,
        trend_direction: Optional[Dict] = None,
    ) -> str:
        g      = metrics.get('gini_coefficient', 0)
        tl     = self._traffic_light(g)
        s, w   = seasonal.get('summer', 0), seasonal.get('winter', 0)

        base = (
            f"The Gini coefficient of {g} indicates {tl['label'].lower()} "
            f"cut distribution across regions."
        )

        if trend_direction and trend_direction['direction'] != 'insufficient_data':
            base += f" {trend_direction['summary']}"

        if abs(s - w) > 0.1:
            base += (
                f" There is a notable seasonal gap: summer Gini is {s} vs "
                f"winter {w}, suggesting some regions bear a disproportionate "
                f"burden during peak periods."
            )

        return base