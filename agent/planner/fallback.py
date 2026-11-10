"""Template planner - used only when no LLM is reachable. It never crashes.

It does NOT understand the topic of the request (that needs an LLM); it builds a
generic but well-structured video for the detected mood/language/length. Add an
LLM key (e.g. NVIDIA_API_KEY) to get topic-specific scripts.
"""
from __future__ import annotations

from agent.planner.detect import (detect_aspect, detect_duration, detect_language, detect_mood,
                                  pick_voice)
from agent.planner.schema import Mood, Plan, Scene

# 5 lines per set: 4 body lines + 1 call-to-action. Keys: (mood group, language)
_LINES = {
    ("motivational", "hi"): [
        "ज़्यादातर लोग सही मौके पर हार मान लेते हैं।",
        "लेकिन जीतने वाले हर दिन फिर से शुरुआत करते हैं।",
        "छोटे कदम, रोज़ाना, बिना रुके।",
        "यही कामयाबी का असली राज़ है।",
        "फॉलो करो और रुकना मत।",
    ],
    ("motivational", "hinglish"): [
        "Zyada tar log sahi moke par haar maan lete hain.",
        "Lekin jeetne wale har din phir se shuruaat karte hain.",
        "Chhote kadam, roz, bina ruke.",
        "Yahi kamyabi ka asli raaz hai.",
        "Follow karo aur rukna mat.",
    ],
    ("motivational", "en"): [
        "Most people quit at exactly the wrong moment.",
        "The winners simply start again every single day.",
        "Small steps. Every day. No excuses.",
        "That is the real secret of success.",
        "Follow for more, and never stop.",
    ],
    ("sad", "hi"): [
        "कभी-कभी सबसे ज़्यादा शोर हमारे अंदर होता है।",
        "हम मुस्कुराते हैं, पर सब ठीक नहीं होता।",
        "कुछ बातें कहने से ज़्यादा सहने में बीत जाती हैं।",
        "पर यकीन रखो, ये वक़्त भी गुज़र जाएगा।",
        "अगर दिल को छुआ हो, तो फॉलो करो।",
    ],
    ("sad", "hinglish"): [
        "Kabhi kabhi sabse zyada shor humare andar hota hai.",
        "Hum muskurate hain, par sab theek nahi hota.",
        "Kuch baatein kehne se zyada sehne mein beet jaati hain.",
        "Par yakeen rakho, ye waqt bhi guzar jayega.",
        "Agar dil ko chhua ho, to follow karo.",
    ],
    ("sad", "en"): [
        "Sometimes the loudest noise is the one inside us.",
        "We smile, even when everything is not okay.",
        "Some things are carried in silence, not spoken.",
        "But hold on. This time will pass too.",
        "If this touched you, follow for more.",
    ],
    ("calm", "hi"): [
        "एक गहरी सांस लो।",
        "आज जो हो रहा है, उसे बस होने दो।",
        "हर सुबह एक नई शुरुआत है।",
        "खुद के साथ थोड़ा वक़्त बिताओ।",
        "सुकून चाहिए तो फॉलो करो।",
    ],
    ("calm", "hinglish"): [
        "Ek gehri saans lo.",
        "Aaj jo ho raha hai, use bas hone do.",
        "Har subah ek nayi shuruaat hai.",
        "Khud ke saath thoda waqt bitao.",
        "Sukoon chahiye to follow karo.",
    ],
    ("calm", "en"): [
        "Take a deep breath.",
        "Let today unfold exactly as it needs to.",
        "Every morning is a fresh start.",
        "Give yourself a little quiet time.",
        "Follow for more calm.",
    ],
    ("dramatic", "hi"): [
        "हर बड़ी कहानी एक छोटे फ़ैसले से शुरू होती है।",
        "कोई नहीं जानता था कि आगे क्या होगा।",
        "मगर उस रात सब कुछ बदल गया।",
        "और यहीं से शुरू हुई असली जंग।",
        "अगला हिस्सा चाहिए तो फॉलो करो।",
    ],
    ("dramatic", "hinglish"): [
        "Har badi kahani ek chhote faisle se shuru hoti hai.",
        "Koi nahi jaanta tha ki aage kya hoga.",
        "Magar us raat sab kuch badal gaya.",
        "Aur yahin se shuru hui asli jung.",
        "Agla hissa chahiye to follow karo.",
    ],
    ("dramatic", "en"): [
        "Every great story begins with one small decision.",
        "Nobody knew what was coming next.",
        "But that night, everything changed.",
        "And that is where the real fight began.",
        "Follow for part two.",
    ],
}

_TITLES = {
    ("motivational", "hi"): "आज की प्रेरणा", ("motivational", "hinglish"): "Aaj Ki Prerna",
    ("motivational", "en"): "Never Quit", ("sad", "hi"): "दिल की बात",
    ("sad", "hinglish"): "Dil Ki Baat", ("sad", "en"): "Hold On",
    ("calm", "hi"): "थोड़ा सुकून", ("calm", "hinglish"): "Thoda Sukoon", ("calm", "en"): "Breathe",
    ("dramatic", "hi"): "एक अनकही कहानी", ("dramatic", "hinglish"): "Ek Ankahi Kahani",
    ("dramatic", "en"): "The Night Everything Changed",
}

_VISUALS = {
    "motivational": ["sunrise over mountain peak, cinematic", "silhouette of man running at dawn",
                     "climbing stairs towards light, cinematic", "hands reaching the summit, golden hour",
                     "city skyline at sunrise, inspiring"],
    "sad": ["rain on a window at night, moody", "lonely person on a bench, blue tones",
            "empty road in fog, melancholy", "city lights blurred in rain",
            "sunrise breaking through clouds, hopeful"],
    "calm": ["calm lake at sunrise, mist", "soft waves on a quiet beach", "green forest with sunbeams",
             "cup of tea by a window, cozy", "starry sky over still water"],
    "dramatic": ["dark stormy sky with lightning, cinematic", "lone figure in a dark alley, noir",
                 "burning city skyline at night", "dramatic close up of eyes, cinematic",
                 "silhouette walking into fog, thriller"],
}

_TAGS = {
    "motivational": ["motivation", "success", "mindset", "reels"],
    "sad": ["emotional", "sad", "feelings", "quotes"],
    "calm": ["lofi", "calm", "aesthetic", "relax"],
    "dramatic": ["cinematic", "story", "drama", "shorts"],
}


def _group(mood: Mood) -> str:
    return "motivational" if mood == Mood.ENERGETIC else mood.value


def fallback_plan(user_request: str, prefs: dict | None = None) -> Plan:
    prefs = prefs or {}
    lang = detect_language(user_request, prefs)
    mood = detect_mood(user_request)
    group = _group(mood)
    aspect = detect_aspect(user_request, prefs)
    dur = detect_duration(user_request)

    lines = _LINES[(group, lang)]
    n = min(5, max(3, round(dur / 6)))
    chosen = lines[: n - 1] + [lines[-1]]
    visuals = _VISUALS[group]
    per = round(dur / n, 1)

    scenes = [Scene(index=i, narration=chosen[i], visual_prompt=visuals[i % len(visuals)],
                    duration_hint=per, transition="fade") for i in range(n)]
    return Plan(
        title=_TITLES[(group, lang)],
        hook=chosen[0],
        script=" ".join(chosen),
        scenes=scenes,
        voice=pick_voice(lang, prefs),
        language=lang,
        music_mood=mood,
        aspect_ratio=aspect,
        caption_style="karaoke",
        hashtags=_TAGS[group],
        description=chosen[0],
        cta=chosen[-1],
        source="template",
    )
