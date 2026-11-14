"""Narration, one entry per scene. `say` is read by TTS (spelled for pronunciation); captions are derived from it with DISPLAY."""
import re

SCENES = [
    dict(id="s01_intro", chip="TerraAegis · HealthPulse",
         say="Hi! We're Terra Aegis, a two-person team from Vietnam. And this is HealthPulse, um, a climate-informed early warning and response planning tool."),
    dict(id="s02_problem", chip="01 · The problem",
         say="Here's the problem. Climate is shifting when and where disease hits, and the I L O estimates two point four billion workers face excessive heat at work. "
             "Yet most health teams still react after cases are reported. By then, uh, the weeks you needed to prepare are gone."),
    dict(id="s03_how", chip="02 · How it works",
         say="HealthPulse turns public climate, disease and population data into forecasts with honest uncertainty ranges, compares demand against resources, "
             "and, this is key, leaves the final decision to a person."),
    dict(id="s04_warned", chip="03 · Would it have warned us?",
         say="Let me show you the working app. First, a hard question: would it have warned us? We replayed Brazil's record twenty twenty-three dengue season, "
             "week by week, using only the data available at the time. All nineteen epidemic states were warned early, a median of six weeks ahead. "
             "To be fair, a simple rule gets four, so the model adds about two."),
    dict(id="s05_replay", chip="04 · Replay 2023–24",
         say="Here's Minas Gerais. The blue line is our alert, in mid-December. The red line is when threshold monitoring would have seen it, late January. "
             "That gap is time to send vector-control teams, and staff up clinics."),
    dict(id="s06_brief", chip="05 · Outlook, AI brief & human decision",
         say="Next, the twenty twenty-six season outlook, with its eighty percent range. Then the brief. Our AI analyst, Claude, writes it live, "
             "from exactly these numbers, with a rule-based fallback if it's offline. And, uh, it can only propose. "
             "An epidemiology reviewer approves or rejects, and it's logged for audit."),
    dict(id="s07_vietnam", chip="06 · Viet Nam · 34 provinces",
         say="Now, Vietnam, our home. All thirty-four new provinces, with NASA weather data and a climate-driven dengue model. "
             "You can see which provinces are on alert this month, and why. Hanoi is estimated at about one point seven times its seasonal norm. "
             "And every number is labelled: observed, estimate, or scenario."),
    dict(id="s08_sandbox", chip="07 · Outbreak sandbox",
         say="In the outbreak sandbox, planners seed an outbreak on the real map, press play, and test distancing, vaccination or vector control, "
             "then watch hospital load across sixteen trials."),
    dict(id="s09_ops", chip="08 · Live response · AI analyst",
         say="And for a real emergency, there's Live response, an incident command room. In this drill, cases are keyed in as they arrive, "
             "and clusters appear on the map. The AI analyst uses tools over the live data to recommend next steps, "
             "and the optimiser proposes where resources go. Again, people approve."),
    dict(id="s10_evidence", chip="09 · Evidence",
         say="So, does it work? On held-out seasons across twenty-six Brazilian states, eighty-three percent of actual counts fell inside our predicted range, "
             "versus seventy-five for the baseline. Better, but, um, still short of our ninety percent target, and we say so."),
    dict(id="s11_vision", chip="10 · What's next",
         say="Next, we want to pilot this with a local health or workplace team in Vietnam, then add validated heat exposure inputs for outdoor workers. "
             "No wearables, no personal data, just a browser. HealthPulse: from signals to earlier decisions. Thank you!"),
]

DISPLAY = [
    ("Terra Aegis", "TerraAegis"), ("I L O", "ILO"), ("two point four billion", "2.4 billion"),
    ("twenty twenty-three", "2023–24"), ("twenty twenty-six", "2026–27"), ("nineteen", "19"), ("six weeks", "6 weeks"),
    ("gets four", "gets 4"), ("about two", "about 2"), ("eighty percent", "80%"), ("thirty-four", "34"), ("one point seven", "1.7"),
    ("sixteen", "16"), ("twenty-six", "26"), ("eighty-three percent", "83%"), ("seventy-five", "75%"), ("ninety percent", "90%"),
    ("Hanoi", "Hà Nội"), ("Vietnam, our home", "Viet Nam, our home"),
]


def display(text: str) -> str:
    for a, b in DISPLAY:
        text = text.replace(a, b)
    text = re.sub(r"\b(um|uh),\s*", "", text)          # captions omit spoken fillers
    text = re.sub(r"\b(And|but), ", r"\1 ", text)
    return text


def caption_chunks(say: str, max_chars: int = 84):
    """Split narration into caption lines (sentence / clause boundaries), returning (spoken_text, display_text) pairs."""
    sents = re.split(r"(?<=[.!?])\s+", say.strip())
    out = []
    for s in sents:
        if len(s) <= max_chars:
            out.append(s)
            continue
        parts, cur = re.split(r"(?<=,)\s+", s), ""
        for p in parts:
            if cur and len(cur) + 1 + len(p) > max_chars:
                out.append(cur)
                cur = p
            else:
                cur = (cur + " " + p).strip()
        if cur:
            out.append(cur)
    return [(c, display(c).strip().rstrip(',').strip() or display(c)) for c in out]
