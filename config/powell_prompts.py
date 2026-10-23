"""
Powell Speech Sentiment Analysis Prompts
-----------------------------------------
LLM prompt templates for hawkish/dovish classification of Fed speeches.
"""

# System prompt for sentiment analysis
SENTIMENT_SYSTEM_PROMPT = """You are an expert Fed watcher and monetary policy analyst. Your task is to analyze excerpts from Federal Reserve speeches (especially Chair Powell) and classify them on a hawkish-to-dovish scale.

HAWKISH signals (positive scores):
- Concerns about persistent inflation
- Strong labor market as reason to maintain rates
- "Higher for longer" rate rhetoric
- Caution about premature easing
- Upside risks to inflation
- Strong economic growth requiring restraint

DOVISH signals (negative scores):
- Progress on inflation / disinflation
- Labor market cooling / normalization
- Openness to rate cuts
- Downside risks to employment
- Economic slowdown concerns
- Financial stress concerns

SCORING GUIDE:
+7 to +10: VERY HAWKISH - Strong commitment to fighting inflation, rates staying high
+3 to +6:  HAWKISH - Cautious about cutting, emphasizing inflation risks
-2 to +2:  NEUTRAL - Balanced, data-dependent, no clear lean
-6 to -3:  DOVISH - Leaning toward cuts, emphasizing progress on inflation
-10 to -7: VERY DOVISH - Clear signal of imminent cuts, employment concerns

Always respond with valid JSON."""

# User prompt template for chunk analysis
SENTIMENT_ANALYSIS_PROMPT = """Analyze this excerpt from a Federal Reserve speech for hawkish/dovish sentiment:

TEXT:
\"\"\"{text}\"\"\"

Respond with JSON containing:
{{
    "score": <float from -10 (very dovish) to +10 (very hawkish)>,
    "topic": <one of: "Employment", "Inflation", "Rate Policy", "Economic Outlook", "Financial Conditions", "Other">,
    "key_phrase": <the most significant phrase that drove your score (max 60 chars)>,
    "reasoning": <brief explanation of your score (max 100 chars)>
}}

Focus on:
1. What does this say about the future path of interest rates?
2. Is the Fed more worried about inflation or employment?
3. Are there signals about upcoming policy changes?

Respond ONLY with the JSON object."""

# Sample Fed speech excerpts for testing
SAMPLE_SPEECHES = [
    # Hawkish examples
    "The labor market remains remarkably strong, with unemployment near historic lows. "
    "While this is good news for American workers, it also means that inflationary pressures "
    "from the labor market may take longer to fully abate.",
    
    "Inflation, while moderating, remains well above our 2 percent target. We are committed "
    "to returning inflation to our goal, and we will keep policy restrictive until we are "
    "confident that inflation is moving sustainably toward 2 percent.",
    
    "We are not in a hurry to lower rates. Given the strength of the economy and the labor "
    "market, we can afford to be patient and let the data guide our decisions. Premature "
    "easing could allow inflation to become entrenched.",
    
    # Neutral examples
    "Our policy decisions will be data-dependent and meeting-by-meeting. We are closely "
    "monitoring incoming data on inflation, employment, and economic activity to assess "
    "the appropriate path of monetary policy.",
    
    "The economy has shown resilience, though we are seeing some normalization in the labor "
    "market. Inflation has come down substantially from its peak, but we need to see more "
    "progress before adjusting our policy stance.",
    
    # Dovish examples
    "We have seen significant progress on inflation over the past year. Price pressures have "
    "moderated across a broad range of goods and services, and inflation expectations remain "
    "well anchored.",
    
    "The labor market, while still healthy, is showing signs of better balance. Job openings "
    "have declined, quits rates have normalized, and wage growth has moderated. This cooling "
    "should help reduce inflationary pressures.",
    
    "We are attentive to the risks on both sides of our mandate. While we remain focused on "
    "price stability, we are also mindful of the risk of keeping policy too tight for too long, "
    "which could harm the labor market unnecessarily.",
    
    "If inflation continues to move down as we expect, it would be appropriate to begin dialing "
    "back policy restraint at some point this year. We want to be confident, but we don't need "
    "to wait until inflation is at 2 percent to start reducing rates.",
    
    "Recent data suggest the economy may be slowing more than anticipated. We are prepared to "
    "adjust policy as needed to support our dual mandate of maximum employment and price stability.",
]

# Topic classification examples (for reference)
TOPIC_EXAMPLES = {
    "Employment": [
        "labor market", "unemployment", "job creation", "hiring", "layoffs",
        "wages", "workforce", "employment", "jobless claims", "payrolls"
    ],
    "Inflation": [
        "inflation", "prices", "CPI", "PCE", "core inflation", "disinflation",
        "price stability", "cost pressures", "inflation expectations"
    ],
    "Rate Policy": [
        "interest rates", "fed funds rate", "rate cuts", "rate hikes", "policy rate",
        "monetary policy", "restrictive", "accommodative", "easing", "tightening"
    ],
    "Economic Outlook": [
        "GDP", "growth", "recession", "expansion", "economic activity",
        "consumer spending", "business investment", "soft landing", "hard landing"
    ],
    "Financial Conditions": [
        "financial conditions", "credit", "lending", "banks", "markets",
        "yields", "spreads", "liquidity", "financial stability"
    ],
}

