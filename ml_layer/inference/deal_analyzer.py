"""
    PropIQ - AI Deal Analyzer
    Uses Claude to generate natural-language investment analysis combining:
        - AVM valuation + SHAP explanation
        - LSTM market forecast
        - Feature-engineered opportunities signals
        - Comparable sales summary

    @author Minh Thang Nguyen
    @version July 8, 2026
"""

from __future__ import annotations

import os, json
from typing import Optional
from dataclasses import dataclass
from loguru import logger
import anthropic

@dataclass
class PropertyContext:
    address: str
    zip_code: str
    list_price: float
    property_type: str
    bedrooms: int
    bathrooms: int
    building_sqft: int
    lot_size_sqft: Optional[int]
    year_built: Optional[int]
    avm_value: float
    avm_confidence: float
    top_value_drivers: list[dict]
    forecast_3mo: float
    forecast_6mo: float
    forecast_12mo: float
    development_score: float
    adu_eligible: bool
    renovation_score: float
    underbuilt_ratio: float
    neighborhood_name: Optional[str]
    neighborhood_median: float
    days_on_market: int
    price_change_yoy: float
    inventory_months: float
    comparables: list[dict]

@dataclass
class DealAnalysis:
    property_address: str
    investment_thesis: str
    value_assessment: str
    market_outlook: str
    opportunities_flags: list[str]
    risk_flags: list[str]
    recommended_actions: list[str]
    deal_score: int
    raw_response: str

def _build_prompt(ctx: PropertyContext) -> str:
    price_diff = ctx.avm_value - ctx.list_price
    price_diff_p = price_diff / ctx.list_price * 100
    comp_lines = "\n".join(
        f"  • {c['address']} - sold ${c['sale_price']:,.0f} ({c['sqft']:,} sqft, {c['date']})"
        for c in ctx.comparables[:5]
    )
    driver_lines = "\n".join(
        f"  • {d['feature']}: {'+' if d['direction'] == 'up' else '-'}${abs(d['impact']):,.0f}"
        for d in ctx.top_value_drivers[:5]
    )
    return f"""You are PropIQ's senior investment analyst AI. Analyze this property investment opportunity and provide a structured, data-driven assessment.

=== PROPERTY ===
Address:       {ctx.address} ({ctx.zip_code})
Type:          {ctx.property_type} | {ctx.bedrooms}bd/{ctx.bathrooms}ba | {ctx.building_sqft:,} sqft
Lot:           {ctx.lot_size_sqft:,} sqft | Built: {ctx.year_built}
List Price:    ${price_diff_p:,.0f}

=== AI VALUATION (AVM) ===
Estimated FMV: ${ctx.avm_value:,.0f} ({'+' if price_diff >= 0 else ''}{price_diff_p:.1f}% vs list price)
Confidence:    {ctx.avm_confidence:.0%}
Top value drivers:
{driver_lines}

=== MARKET FORECAST (LSTM) ===
Neighborhood median: ${ctx.neighborhood_median:,.0f}
YoY price change:   {ctx.price_change_yoy:+.1f}%
Forecast:
  • 3-month:  {ctx.forecast_3mo:+.1f}%
  • 6-month:  {ctx.forecast_6mo:+.1f}%
  • 12-month: {ctx.forecast_12mo:+.1f}%
Days on market:    {ctx.days_on_market}
Inventory:         {ctx.inventory_months:.1f} months

=== OPPORTUNITY SIGNALS ===
Development score:  {ctx.development_score:.0f}/100
ADU eligible:       {'Yes' if ctx.adu_eligible else 'No'}
Renovation score:   {ctx.renovation_score:.0f}/100
Underbuilt ratio:   {ctx.underbuilt_ratio:.0%}

=== COMPARABLE SALES ===
{comp_lines if comp_lines else '  No comps available'}

=== YOUR ANALYSIS ===
Provide a structured investment analysis in this exact JSON format:
{{
  "investment_thesis": "<2-3 sentence summary of the deal>",
  "value_assessment": "<narrative on AVM vs list price and what it means>",
  "market_outlook": "<narrative on market forecast and timing>",
  "opportunity_flags": ["<opportunity 1>", "<opportunity 2>", ...],
  "risk_flags": ["<risk 1>", "<risk 2>", ...],
  "recommended_actions": ["<action 1>", "<action 2>", ...],
  "deal_score": <integer 0-100>
}}

Be specific, data-driven, and concise. Reference actual numbers from the data.
Respond ONLY with valid JSON, no preamble or explanation."""

class DealAnalyzer:
    MODEL = "claude-sonnet-4-6"

    def __init__(self, api_key: Optional[str] = None):
        self.client = anthropic.Anthropic(api_key=api_key or os.environ["ANTHROPIC_API_KEY"])

    def analyze(self, ctx: PropertyContext) -> DealAnalysis:
        prompt = _build_prompt(ctx)
        logger.info(f"DealAnalyzer: calling Claude for {ctx.address}")
        try:
            message = self.client.messages.create(
                model = self.MODEL, max_tokens=1200,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = message.content[0].text.strip()
            data = json.loads(raw)
            return DealAnalysis(
                property_address = ctx.address,
                investment_thesis = data.get("investment_thesis", ""),
                value_assessment = data.get("value_assessment", ""),
                market_outlook = data.get("market_outlook", ""),
                opportunities_flags = data.get("opportunity_flags", []),
                risk_flags = data.get("risk_flags", []),
                recommended_actions = data.get("recommended_actions", []),
                deal_score = int(data.get("deal_score", 50)),
                raw_response = raw,
            )
        except json.JSONDecodeError as e:
            logger.error(f"DealAnalyzer: failed to parse Claude response: {e}")
            raise
        except anthropic.APIError as e:
            logger.error(f"DealAnalyzer: Anthropic API error: {e}")
            raise

    def analyze_batch(self, contexts: list[PropertyContext]) -> list[DealAnalysis]:
        results = []
        for ctx in contexts:
            try:
                results.append(self.analyze(ctx))
            except Exception as e:
                logger.error(f"DealAnalyzer: failed for {ctx.address}: {e}")
        return results