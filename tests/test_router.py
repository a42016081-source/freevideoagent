import pytest

from agent.router import route


@pytest.mark.parametrize("text,intent", [
    ("ek 30 second ka motivational reel bana", "video"),
    ("make a youtube short about space", "video"),
    ("मोटिवेशनल वीडियो बनाओ", "video"),
    ("video with nice images of mountains", "video"),
    ("sunset ki photo bana do", "image"),
    ("draw a logo for my cafe", "image"),
    ("trending", "trends"),
    ("research latest AI chips news", "research"),
    ("what is photosynthesis", "ask"),
    ("python me list sort kaise karte hain", "ask"),
])
def test_routes(text, intent):
    assert route(text)["intent"] == intent
