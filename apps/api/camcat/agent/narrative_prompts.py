"""Ground each narrative decision in observed footage, states and dialogue."""

import re

SOURCE_EVENTS_SYSTEM = (
    "Inspect the supplied actual frames in source-time order. Describe opening state, "
    "events, closing state, actors and location. Identify the concrete visible objects accurately, "
    "including weapons, vehicles and face coverings. Use the user story overview only as context "
    "for recognizing objects; describe ONLY events actually visible in these frames. A reflection "
    "or illumination change does not prove a physical transformation. Do not invent a change of "
    "eye color, intention, possession or survival from a blur or a cut. State uncertain if the "
    "frame does not establish it. In visible_dialogue, transcribe the burned-in dialogue subtitles "
    "in source-time order, without repetitions; empty array when absent. Do not infer overall "
    "clip chronology. Return concise factual JSON."
)

DRAFT_SYSTEM = (
    "Reconstruct one coherent narrative from the shuffled original source clips. Each source "
    "contains automatic observations of its opening state, events in source-time order, "
    "closing state, and visible actors. Use actor appearances, object possession, location "
    "and changes of state to connect the original sources. The story overview specifies the "
    "causal arc; some introductory events in the overview may occur outside the available "
    "excerpt. Place the earliest AVAILABLE event first and the final observed consequence "
    "last. A departure precedes travel and arrival; acquiring an object precedes wielding it;"
    " a confrontation precedes death and subsequent recovery of an object. Distinguish "
    "similar actions performed by different visible actors. Check the whole chain, especially"
    " between shots in the same setting. Use EVERY source exactly once. Neither input order "
    "nor opaque source IDs implies chronology. Return JSON clips, each with material_id and "
    "one decisive evidence ID belonging to that source. The server retains full source ranges"
    " and handles native editing commands."
)

REVIEW_SYSTEM = (
    "Review the proposed narrative chronology against the actual observations and return a "
    "corrected full sequence. First report concise continuity errors supported by source "
    "evidence in audit. Check visible appearance, object possession, state changes, location,"
    " opening and closing states, and actual source-relative speech. The synopsis is a coarse"
    " overview: initial events can occur outside this excerpt. The observed state has "
    "priority over matching a broad action word in the synopsis. An action performed while an"
    " actor is already in a changed state cannot establish the cause of that same state. "
    "Distinguish injury aftermath from the start of a fight, and a final confrontation from "
    "earlier corridor encounters. Prefer a measured spatially distributed source-end/target-"
    "start image match as evidence of direct continuity, while allowing shot changes without "
    "a match. A valid sequence must connect every original exactly once without unexplained "
    "reversals of visible state. Don't preserve the proposed order when evidence contradicts "
    "it. Filename and input position carry no chronology. Return JSON audit and clips. Each "
    "clip has material_id and exactly one evidence_ids citation belonging to that source."
)

PAIR_SYSTEM = (
    "Compare two source clips using their ACTUAL images. Record the primary actor's specific "
    "visible face/eye appearance, face covering, held objects, location and physical "
    "condition separately for A and B BEFORE deciding order. Identify each actor by visible "
    "attributes rather than the broad action they perform. A and B are arbitrary labels, "
    "never chronological positions. Use the supplied story overview to interpret causal "
    "changes of actor/object state, while allowing its initial events to be outside the "
    "available clips. A later repetition of an action does not establish an earlier cause. "
    "Check whether an actor already visibly has a changed appearance or possession before "
    "inferring which event caused it. Blood in DIFFERENT physical locations is independent "
    "evidence: a shooting in one location does not establish the cause of blood at another "
    "location. Clean versus bloody environments support order only if the actual scene and "
    "affected people or objects match. Different rooms do not share the same cleanliness "
    "state. Return uncertain if no ordering is supported. Base certainty on specific visible "
    "or speech links, with no invented offscreen actions. Return concise JSON."
)

OCR_SYSTEM = (
    "You transcribe visible text exactly from images. Source names are only identifiers. "
    "Return JSON with one row for every source."
)

THEMATIC_BLOCK_SYSTEM = (
    "Arrange every supplied source block exactly once. Use the user story overview as the "
    "intended narrative arc, supported by the actual source observations. For a thematic montage "
    "or commercial, align the observed topics to the progression of features stated in the "
    "overview, from introduction through demonstrated capabilities to the conclusion. For a "
    "causal story, connect visible actor and object states, dialogue, movement and consequences. "
    "An overview can mention events outside the supplied excerpt; order only the observed "
    "material. Treat matching subtitle blocks as indivisible. Identifiers and input order have "
    "no temporal meaning. Return the requested JSON block IDs."
)

BLOCK_SYSTEM = (
    "Arrange all supplied source blocks into the coherent narrative chronology. The original "
    "clips were randomly shuffled; identifiers and input order have no temporal meaning. "
    "Sources inside a block have independently transcribed matching burned-in dialogue at the"
    " ending/opening boundary, so their order is fixed and they must remain adjacent. Return "
    "every block exactly once. Respect confirmed draft corrections from direct multimodal "
    "actor-state comparisons. Connect states across blocks using visible actor appearance, "
    "held objects, location, movement, dialogue and causal changes. A source showing an actor"
    " ALREADY in a changed state belongs after the observed event that causes that state. The"
    " synopsis can include initial events outside this excerpt: the earliest AVAILABLE "
    "observed event starts the sequence. Similar verbs do not establish that two events are "
    "the same event. A departure precedes travel and arrival. A confrontation and gunfire "
    "precede corresponding casualties. Clean and bloody DIFFERENT rooms have independent "
    "physical states. Camera framing or a stylistic cut alone never proves temporal order. "
    "Keep the entire observed story causally consistent from beginning to end. Return JSON "
    "blocks, with their IDs only. The server preserves complete originals."
)


def block_instructions(brief: str) -> str:
    """Honor an explicitly described montage; otherwise connect observed causal states."""
    if re.search(
        r"\b(?:commercial|advertisement|montage|promotional video)\b|广告|宣传片|主题蒙太奇",
        brief,
        flags=re.IGNORECASE,
    ):
        return THEMATIC_BLOCK_SYSTEM
    return BLOCK_SYSTEM
