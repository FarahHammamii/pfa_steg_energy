import pandas as pd
import numpy as np
from typing import Dict, Any, List
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

GINI_THRESHOLD = 0.4
GINI_TARGET    = 0.3
SUMMER_MONTHS  = {6, 7, 8, 9}
WINTER_MONTHS  = {12, 1, 2, 3}


class FairnessAgent(BaseAgent):
    """Ensures cuts are distributed fairly across regions"""

    def __init__(self):
        super().__init__("Fairness Agent")
        self.data_loader = DataLoader()

    # ─────────────────────────────────────────────────────────────────────────
    # Public entry points
    # ─────────────────────────────────────────────────────────────────────────

    def analyze(self) -> Dict[str, Any]:
        """Full monthly fairness analysis — main entry point."""

        cut_history = self._load_cut_history()
        logger.info(f"Loaded cut history: {len(cut_history)} records")

        if len(cut_history) == 0:
            return {
                'status': 'no_data',
                'message': 'No cut history available. Run synthetic data generator first.',
                'fairness_score': 0,
                'most_cut_regions': [],
                'least_cut_regions': [],
                'unfair_regions': [],
                'recommendations': ['Generate synthetic cut history to enable fairness analysis'],
                'needs_rebalancing': False
            }

        # Core metrics
        fairness_metrics  = self._calculate_fairness_metrics(cut_history)
        seasonal_metrics  = self._calculate_seasonal_gini(cut_history)
        monthly_trend     = self._calculate_monthly_gini_trend(cut_history)
        unfair_regions    = self._identify_unfair_regions(cut_history)
        double_penalty    = self._detect_double_penalty(cut_history)
        recommendations   = self._generate_fairness_recommendations(
            unfair_regions, fairness_metrics, double_penalty
        )

        # Rotation schedule if needed
        rotation = {}
        if fairness_metrics['gini_coefficient'] > GINI_THRESHOLD:
            cut_counts = (
                cut_history.groupby('region')
                .size()
                .reset_index(name='cut_count')
            )
            rotation = self._generate_rotation_schedule(cut_counts)

        # LLM explanation
        if client and (unfair_regions or double_penalty):
            explanation = self._get_llm_explanation(
                fairness_metrics, seasonal_metrics,
                unfair_regions, double_penalty, recommendations
            )
        else:
            explanation = self._get_fallback_explanation(
                fairness_metrics, seasonal_metrics
            )

        return {
            'status':               'success',
            'fairness_score':       fairness_metrics['gini_coefficient'],
            'seasonal_gini':        seasonal_metrics,
            'monthly_trend':        monthly_trend,
            'most_cut_regions':     fairness_metrics['most_cut'],
            'least_cut_regions':    fairness_metrics['least_cut'],
            'unfair_regions':       unfair_regions,
            'double_penalty':       double_penalty,
            'rotation_schedule':    rotation,
            'recommendations':      recommendations,
            'needs_rebalancing':    fairness_metrics['gini_coefficient'] > GINI_THRESHOLD,
            'total_cuts_analyzed':  fairness_metrics['total_cuts'],
            'explanation':          explanation,
        }

    def validate_cut_list(self, proposed_regions: List[str]) -> Dict[str, Any]:
        """
        Cross-validation gate — called by Cut Advisor BEFORE finalising cuts.
        Simulates adding proposed cuts and checks if Gini would exceed threshold.

        Returns
        -------
        {
          'approved':     True | False,
          'gini_before':  0.28,
          'gini_after':   0.43,
          'substitution': 'Zaghouan' | None,
          'message':      '...'
        }
        """
        cut_history = self._load_cut_history()

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

        gini_before = self._gini_from_counts(list(cut_counts.values()))

        # Simulate adding the proposed cuts
        simulated = dict(cut_counts)
        for region in proposed_regions:
            simulated[region] = simulated.get(region, 0) + 1

        gini_after = self._gini_from_counts(list(simulated.values()))

        if gini_after <= GINI_THRESHOLD:
            return {
                'approved':     True,
                'gini_before':  round(gini_before, 4),
                'gini_after':   round(gini_after, 4),
                'substitution': None,
                'message':      f'Approved. Gini stays at {round(gini_after, 4)} (≤ {GINI_THRESHOLD}).'
            }

        # Suggest fairer substitute — least-cut region not already proposed
        candidate_pool = set(cut_counts.keys()) - set(proposed_regions)
        substitution = (
            min(candidate_pool, key=lambda r: cut_counts.get(r, 0))
            if candidate_pool else None
        )

        return {
            'approved':     False,
            'gini_before':  round(gini_before, 4),
            'gini_after':   round(gini_after, 4),
            'substitution': substitution,
            'message': (
                f'REJECTED: cuts would raise Gini {round(gini_before,4)} → {round(gini_after,4)} '
                f'(threshold {GINI_THRESHOLD}). '
                + (f"Suggest swapping a region for '{substitution}'." if substitution else '')
            )
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Data loading
    # ─────────────────────────────────────────────────────────────────────────

    def _load_cut_history(self) -> pd.DataFrame:
        """Load historical cut data from silver.cut_history."""
        query = """
            SELECT region, cut_date, duration_minutes, season
            FROM silver.cut_history
            ORDER BY cut_date DESC
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

    # ─────────────────────────────────────────────────────────────────────────
    # Gini math
    # ─────────────────────────────────────────────────────────────────────────

    def _gini_from_counts(self, values: List[float]) -> float:
        """
        Standard Gini coefficient.
        0.0 = perfectly equal, 1.0 = perfectly unequal.
        """
        if not values or sum(values) == 0:
            return 0.0
        arr = np.sort(np.array(values, dtype=float))
        n   = len(arr)
        idx = np.arange(1, n + 1)
        return float((2 * np.sum(idx * arr) - (n + 1) * np.sum(arr)) / (n * np.sum(arr)))

    def _gini_from_duration(self, df: pd.DataFrame) -> float:
        """
        Duration-weighted Gini — uses total cut hours per region,
        not just cut count. A 10-minute cut and a 6-hour cut are not equal.
        """
        if len(df) == 0:
            return 0.0
        duration_by_region = df.groupby('region')['duration_minutes'].sum()
        return self._gini_from_counts(duration_by_region.tolist())

    # ─────────────────────────────────────────────────────────────────────────
    # Core fairness metrics
    # ─────────────────────────────────────────────────────────────────────────

    def _calculate_fairness_metrics(self, cut_history: pd.DataFrame) -> Dict[str, Any]:
        """Overall Gini by count and by duration."""

        if len(cut_history) == 0:
            return {'gini_coefficient': 0, 'most_cut': [], 'least_cut': [], 'total_cuts': 0}

        cut_counts = (
            cut_history.groupby('region')
            .agg(cut_count=('region', 'size'), total_minutes=('duration_minutes', 'sum'))
            .reset_index()
        )

        gini_by_count    = self._gini_from_counts(cut_counts['cut_count'].tolist())
        gini_by_duration = self._gini_from_duration(cut_history)

        most_cut  = cut_counts.nlargest(3, 'cut_count')[['region', 'cut_count']].to_dict('records')
        least_cut = cut_counts.nsmallest(3, 'cut_count')[['region', 'cut_count']].to_dict('records')

        return {
            'gini_coefficient':    round(gini_by_count, 3),
            'gini_by_duration':    round(gini_by_duration, 3),
            'most_cut':            most_cut,
            'least_cut':           least_cut,
            'total_cuts':          len(cut_history),
        }

    def _calculate_seasonal_gini(self, cut_history: pd.DataFrame) -> Dict[str, Any]:
        """
        Separate Gini for summer vs winter.
        A region cut more in summer than winter may look fair in the
        annual average but is being penalised during peak stress periods.
        """
        if len(cut_history) == 0 or 'season' not in cut_history.columns:
            return {'summer': 0.0, 'winter': 0.0, 'note': 'no season data'}

        summer = cut_history[cut_history['season'].str.lower() == 'summer']
        winter = cut_history[cut_history['season'].str.lower() == 'winter']

        gini_summer = self._gini_from_duration(summer) if len(summer) > 1 else 0.0
        gini_winter = self._gini_from_duration(winter) if len(winter) > 1 else 0.0

        # Flag regions that appear fair annually but unfair seasonally
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
                    seasonal_flags.append(f'{region}: {s} summer vs {w} winter')

        return {
            'summer':         round(gini_summer, 3),
            'winter':         round(gini_winter, 3),
            'seasonal_flags': seasonal_flags,
        }

    def _calculate_monthly_gini_trend(self, cut_history: pd.DataFrame) -> List[Dict]:
        """
        Cumulative Gini month by month — shows whether fairness
        is improving or getting worse over time.
        """
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
                    'region':         row['region'],
                    'issue':          'cut_too_often',
                    'cut_count':      int(row['cut_count']),
                    'above_average_by': int(row['cut_count'] - avg),
                })
            elif row['cut_count'] < avg - std:
                unfair.append({
                    'region':         row['region'],
                    'issue':          'cut_too_rarely',
                    'cut_count':      int(row['cut_count']),
                    'below_average_by': int(avg - row['cut_count']),
                })

        return unfair

    def _detect_double_penalty(self, cut_history: pd.DataFrame) -> List[Dict]:
        """
        Regions with BOTH below-average solar investment AND above-average cuts.
        These are already disadvantaged economically and being cut more — 
        a double penalty.
        """
        investment_df = self._load_solar_investment()

        if len(investment_df) == 0 or len(cut_history) == 0:
            return []

        cut_counts = cut_history.groupby('region').size().reset_index(name='cut_count')
        merged     = cut_counts.merge(investment_df, on='region', how='inner')

        if len(merged) == 0:
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
    # Rotation schedule
    # ─────────────────────────────────────────────────────────────────────────

    def _generate_rotation_schedule(
        self, cut_counts: pd.DataFrame, cuts_needed: int = 8
    ) -> Dict[str, Any]:
        """
        When Gini > 0.4: produce a 4-week calendar that would bring
        the Gini back under GINI_TARGET (0.3).

        Assigns cuts to least-cut regions first, cycling through all
        regions to spread load as evenly as possible.
        """
        if len(cut_counts) == 0:
            return {}

        # Sort ascending — least cut regions go first
        sorted_regions = (
            cut_counts.sort_values('cut_count')['region'].tolist()
        )
        n             = len(sorted_regions)
        cuts_per_week = max(1, cuts_needed // 4)

        schedule: Dict[str, List[str]] = {}
        idx = 0
        for week in range(1, 5):
            week_regions = []
            for _ in range(cuts_per_week):
                week_regions.append(sorted_regions[idx % n])
                idx += 1
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
        unfair_regions: List[Dict],
        metrics:        Dict,
        double_penalty: List[Dict],
    ) -> List[str]:

        recommendations = []

        for region in unfair_regions:
            if region['issue'] == 'cut_too_often':
                recommendations.append(
                    f"Reduce cut frequency in {region['region']} — "
                    f"cut {region['cut_count']} times vs average."
                )
            else:
                recommendations.append(
                    f"Increase cut priority for {region['region']} — "
                    f"only cut {region['cut_count']} times."
                )

        for region in double_penalty:
            recommendations.append(
                f"Double-penalty alert: {region['region']} has low solar investment "
                f"({region['investment_gap_pct']}% below average) AND "
                f"{region['cut_excess_pct']}% more cuts than average — severity: {region['severity']}."
            )

        if metrics['gini_coefficient'] > GINI_THRESHOLD:
            recommendations.append(
                "High inequality detected — implement rotating cut schedule across all regions."
            )

        if metrics.get('gini_by_duration', 0) > metrics.get('gini_coefficient', 0) + 0.1:
            recommendations.append(
                "Duration-weighted Gini is significantly higher than count-based Gini — "
                "some regions are experiencing longer cuts, not just more frequent ones."
            )

        if not recommendations:
            recommendations.append("Cut distribution is fair. No adjustments needed.")

        return recommendations

    # ─────────────────────────────────────────────────────────────────────────
    # LLM explanation
    # ─────────────────────────────────────────────────────────────────────────

    def _get_llm_explanation(
        self,
        metrics:        Dict,
        seasonal:       Dict,
        unfair_regions: List[Dict],
        double_penalty: List[Dict],
        recommendations: List[str],
    ) -> str:

        context = f"""
Fairness Metrics:
- Gini coefficient (count-based):    {metrics['gini_coefficient']}
- Gini coefficient (duration-based): {metrics.get('gini_by_duration', 'N/A')}
- Total cuts analyzed: {metrics['total_cuts']}
- Most cut region:  {metrics['most_cut'][0]['region'] if metrics['most_cut'] else 'N/A'} ({metrics['most_cut'][0]['cut_count'] if metrics['most_cut'] else 0} times)
- Least cut region: {metrics['least_cut'][0]['region'] if metrics['least_cut'] else 'N/A'} ({metrics['least_cut'][0]['cut_count'] if metrics['least_cut'] else 0} times)

Seasonal Gini:
- Summer Gini: {seasonal.get('summer', 'N/A')}
- Winter Gini: {seasonal.get('winter', 'N/A')}
- Seasonal flags: {', '.join(seasonal.get('seasonal_flags', [])) or 'None'}

Unfair Regions:
{', '.join([f"{r['region']} ({r['issue']})" for r in unfair_regions]) or 'None'}

Double-Penalty Regions (low investment + high cuts):
{', '.join([f"{r['region']} (severity: {r['severity']})" for r in double_penalty]) or 'None'}

Recommendations:
{chr(10).join(recommendations)}
"""

        prompt = f"""
You are STEG's fairness auditor for electricity grid management in Tunisia.

INSTRUCTIONS:
- Answer ONLY using the provided context.
- DO NOT use your prior knowledge.
- Explain what the fairness metrics mean in plain language.
- Highlight any seasonal unfairness or double-penalty regions.
- Keep explanation to 3-4 sentences.

==================== CONTEXT ====================
{context}

==================== QUESTION ====================
Is the current distribution of power cuts fair across regions?
What does the Gini coefficient tell us, and are there any regions
being disproportionately affected?

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
            return self._get_fallback_explanation(metrics, seasonal)

    def _get_fallback_explanation(self, metrics: Dict, seasonal: Dict) -> str:
        g = metrics.get('gini_coefficient', 0)
        s = seasonal.get('summer', 0)
        w = seasonal.get('winter', 0)

        base = (
            f"The Gini coefficient of {g} indicates "
            + ("significant inequality" if g > GINI_THRESHOLD else "relatively fair distribution")
            + " in cut distribution across regions."
        )

        if abs(s - w) > 0.1:
            base += (
                f" There is a notable seasonal gap: summer Gini is {s} vs winter {w}, "
                f"suggesting some regions bear a disproportionate burden during peak periods."
            )

        return base