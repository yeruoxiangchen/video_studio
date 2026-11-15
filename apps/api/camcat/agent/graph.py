from __future__ import annotations

import json
import logging
import math
import time
from typing import Any, Literal, TypedDict, cast

from langgraph.graph import END, START, StateGraph
from pydantic import TypeAdapter, ValidationError

from camcat.agent.caption_repair import (
    protected_caption_terms,
    repair_caption_text,
    retains_caption_terms,
)
from camcat.agent.context import (
    compact_materials,
    planning_snapshot,
    prompt_schema,
    source_duration_limits,
)
from camcat.agent.contract import (
    CommandProposal,
    InitialCommandProposal,
    PrimaryClipProposal,
    RefinementCommandProposal,
    RevisionCommandProposal,
    command_proposal_schema,
    refinement_proposal_schema,
)
from camcat.agent.errors import AgentInputError, EditPlanRejected
from camcat.agent.intent import RequirementIntent
from camcat.agent.narrative import narrative_ordering_requested
from camcat.agent.persistence import StatePersistenceService
from camcat.agent.scope import editing_retrieval_filters
from camcat.agent.sequence import (
    SEQUENCE_SYSTEM,
    SourceSequenceProposal,
    material_aliases,
    sequence_commands,
    sequence_payload,
    source_filenames,
)
from camcat.agent.sequence_evidence import (
    EVIDENCE_SEQUENCE_SYSTEM,
    EvidenceSequenceProposal,
    evidence_materials,
    resolve_evidence,
)
from camcat.agent.storyboard import select_storyboard, storyboard_slots
from camcat.agent.trace import attach_failure_trace
from camcat.domain.commands import (
    AddAudioSegment,
    AddTrack,
    DomainEditCommand,
    InsertSegment,
    RegisterSource,
    RemoveSegment,
    ReplaceSegmentMedia,
    SetRenderPolicy,
    SetSegmentTransform,
    UpdateCanvas,
    UpdateTextSegment,
)
from camcat.domain.project import (
    AudioSegment,
    AudioTrack,
    Canvas,
    EditingProjectV2,
    MediaSourceRef,
    TextSegment,
    TextTrack,
    Transform,
    VideoSegment,
    VideoTrack,
)
from camcat.domain.reducer import reduce_commands
from camcat.domain.render_policy import RenderingPolicy
from camcat.editing.captions import source_captions as _source_captions
from camcat.editing.linked_audio import linked_background_audio_commands
from camcat.editing.policies import (
    choose_aspect_ratio,
    explicit_external_ratio,
    explicit_source_range,
    prepare_source_candidates,
    requested_aspect_ratio,
)
from camcat.editing.requirements import (
    delivery_constraints,
    explicit_music_policy,
    validate_delivery,
)
from camcat.retrieval.service import RetrievalService
from camcat.services.narrative import NativeNarrativePlanner
from camcat.services.providers import ProviderOutputError, QwenChatClient
from camcat.services.telemetry import capture_provider_calls
from camcat.timeline.compiler import TimelineCompilerV2
from camcat.timeline.frames import us_range_to_frames


class VisibleTimelineError(ValueError):
    """A planned text/audio track would fail the native compiler's visible bounds."""


class RequestedSourceRangeError(ValueError):
    """A structurally valid plan does not honor the user's absolute source crop."""


class CaptionRewriteError(ValueError):
    """A caption rewrite changes the script or fails to shorten the wording."""


class CamCatState(TypedDict, total=False):
    mode: Literal["search", "edit"]
    query_text: str
    query_image_base64: str
    explicit_filters: dict[str, Any]
    top_k: int
    current_document: dict[str, Any]
    base_version: int
    intent: dict[str, Any]
    filters: dict[str, Any]
    ranked_materials: list[dict[str, Any]]
    source_materials: list[dict[str, Any]]
    domain_commands: list[dict[str, Any]]
    simulated_document: dict[str, Any]
    final_answer: str
    route_sequence: list[str]
    node_trace: list[dict[str, Any]]
    session_id: str
    owner_id: str
    persistence_reason: str
    persisted_version: int
    persisted_document: dict[str, Any]
    planning_recovery: str | None


class CamCatGraph:
    def __init__(
        self,
        *,
        llm: QwenChatClient,
        retrieval: RetrievalService,
        persistence: StatePersistenceService | None = None,
        narrative: NativeNarrativePlanner | None = None,
    ) -> None:
        self.llm = llm
        self.retrieval = retrieval
        self.persistence = persistence or StatePersistenceService()
        self.narrative = narrative
        graph = StateGraph(CamCatState)
        graph.add_node(
            "understand_requirement", self._traced("understand_requirement", self.understand)
        )
        graph.add_node("plan_query", self._traced("plan_query", self.plan_query))
        graph.add_node("retrieve_material", self._traced("retrieve_material", self.retrieve))
        graph.add_node(
            "generate_edit_plan", self._traced("generate_edit_plan", self.generate_commands)
        )
        graph.add_node(
            "generate_subtitles", self._traced("generate_subtitles", self.generate_subtitles)
        )
        graph.add_node("validate_project", self._traced("validate_project", self.validate_project))
        graph.add_node("persistence", self._traced("persistence", self.persist))
        graph.add_edge(START, "understand_requirement")
        graph.add_edge("understand_requirement", "plan_query")
        graph.add_edge("plan_query", "retrieve_material")
        graph.add_conditional_edges(
            "retrieve_material",
            lambda state: state.get("mode", "search"),
            {"search": END, "edit": "generate_edit_plan"},
        )
        graph.add_edge("generate_edit_plan", "generate_subtitles")
        graph.add_edge("generate_subtitles", "validate_project")
        graph.add_edge("validate_project", "persistence")
        graph.add_edge("persistence", END)
        self.compiled = graph.compile()

    def invoke(self, state: CamCatState) -> CamCatState:
        return cast(CamCatState, self.compiled.invoke(self._initial(state)))

    def stream(self, state: CamCatState) -> Any:
        return self.compiled.stream(self._initial(state), stream_mode="values")

    @staticmethod
    def _initial(state: CamCatState) -> CamCatState:
        return {
            **state,
            "route_sequence": [],
            "node_trace": [],
            "explicit_filters": state.get("explicit_filters", {}),
        }

    def understand(self, state: CamCatState) -> dict[str, Any]:
        result = self.llm.json_completion(
            system=(
                "You are CamCat's requirement-understanding node. Return strict JSON with "
                "search_query, target_duration_seconds, style, event_type, tags, platform, "
                "story_arc, pacing, operation_scope, planning_mode, caption_language, "
                "caption_operation, video_transform_requested, response_summary, "
                "and delivery_constraints. planning_mode is source_sequence only when the "
                "requested edit solely selects/orders complete uploaded originals, without "
                "additional effects, speed changes, new audio or text. For other editing "
                "operations use commands. "
                "caption_language is null unless the user explicitly requests a target subtitle "
                "language or supplies replacement caption words in that language. The language "
                "of the editing instruction alone never requests caption translation. "
                "caption_operation is shorten for requests for shorter caption wording, "
                "rewrite for other wording edits, translate for explicit translation, and "
                "keep when no caption wording change is requested. "
                "video_transform_requested is true only for an explicit request to move, "
                "scale/zoom, rotate or change the opacity of the video frame. A reference image, "
                "energetic style "
                "or an output aspect ratio alone does not request a transform; the renderer "
                "fits and centers the source automatically. "
                "delivery_constraints has transitions/subtitles (auto|allow|forbid), "
                "full_source_clips and use_all_sources (booleans), "
                "preserve_color and preserve_audio (boolean or null; null when unspecified), "
                "and clip_count (integer or null). "
                "Follow explicit prohibitions and the latest correction. full_source_clips means "
                "each selected original file in its entirety at original speed; clip_count is "
                "only an explicitly required number of video clips, otherwise null. "
                "operation_scope must be structural for splitting a clip, inserting or deleting "
                "segments, reordering, adding tracks, or replacing media. Use refine for changes "
                "to existing timing, subtitles, transforms or audio settings. "
                "Use auto for unspecified subtitle and transition preferences; a publishing "
                "goal alone never prohibits them. Keep existing source in/out points is "
                "different from using entire source files. Return the fields at the top "
                "level, without wrappers, schema annotations or invented reference URLs. "
                "User footage is always primary."
            ),
            user=state.get("query_text", "用参考图片寻找相似素材"),
            response_schema=RequirementIntent.model_json_schema(),
        )
        try:
            result = RequirementIntent.model_validate(result).model_dump(mode="json")
        except ValidationError:
            raise ProviderOutputError("requirement understanding returned invalid fields") from None
        project_payload = state.get("current_document")
        project = EditingProjectV2.model_validate(project_payload) if project_payload else None
        source_media = project.metadata.get("source_media", []) if project else []
        first = source_media[0] if source_media else {}
        has_timeline = project is not None and any(
            track.segments for track in project.timeline.tracks
        )
        width = project.canvas.width if project and has_timeline else int(first.get("width") or 0)
        height = (
            project.canvas.height if project and has_timeline else int(first.get("height") or 0)
        )
        result["aspect_ratio"] = choose_aspect_ratio(
            state.get("query_text", ""),
            width,
            height,
        )
        result["external_material_ratio_limit"] = explicit_external_ratio(
            state.get("query_text", "")
        )
        result["delivery_constraints"] = delivery_constraints(
            state.get("query_text", ""), result.get("delivery_constraints")
        ).model_dump()
        return {
            "intent": result,
            "final_answer": str(result.get("response_summary", "已理解需求。")),
        }

    def plan_query(self, state: CamCatState) -> dict[str, Any]:
        intent = state["intent"]
        filters = dict(state.get("explicit_filters", {}))
        if intent.get("event_type"):
            filters.setdefault("event_type", str(intent["event_type"]))
        if isinstance(intent.get("tags"), list) and intent["tags"]:
            filters.setdefault("tags", [str(item) for item in intent["tags"]])
        return {
            "filters": filters,
            "route_sequence": ["dense", "bm25", "scalar", "business_fusion", "qwen_vl_rerank"],
        }

    def retrieve(self, state: CamCatState) -> dict[str, Any]:
        query = str(state.get("intent", {}).get("search_query") or state.get("query_text") or "")
        originals_only = (
            state.get("mode") == "edit"
            and state.get("intent", {}).get("external_material_ratio_limit") == 0
        )
        materials = (
            []
            if originals_only
            else self.retrieval.search(
                query_text=query or None,
                query_image_base64=state.get("query_image_base64"),
                filters=state.get("filters", {}),
                top_k=int(state.get("top_k", 8)),
            )
        )
        ranked = [
            {
                "segment_id": item.segment_id,
                "score": item.score,
                "reranker_score": item.reranker_score,
                "entity": item.entity,
                "route_scores": item.route_scores,
                "route_ranks": item.route_ranks,
            }
            for item in materials
        ]
        source_segments = []
        if state.get("current_document"):
            project = EditingProjectV2.model_validate(state["current_document"])
            source_segments = cast(
                list[dict[str, Any]], project.metadata.get("source_segments", [])
            )
        sources = prepare_source_candidates(source_segments)
        return {
            "ranked_materials": ranked,
            "source_materials": sources,
            "final_answer": (
                f"{state.get('final_answer', '')} 已召回并重排 {len(ranked)} 个候选片段。"
            ),
        }

    def generate_commands(self, state: CamCatState) -> dict[str, Any]:
        project = EditingProjectV2.model_validate(state["current_document"])
        candidates, registry = self._candidate_registry(state, project)
        intent = state["intent"]
        external_ratio_limit = float(intent.get("external_material_ratio_limit", 0.25))
        if external_ratio_limit == 0:
            candidates = [item for item in candidates if item["origin"] == "user_upload"]
        candidates = compact_materials(candidates, project)
        requirements = delivery_constraints(
            state.get("query_text", ""), intent.get("delivery_constraints")
        )
        current_policy = RenderingPolicy.model_validate(project.metadata.get("render_policy") or {})
        requested_policy = {
            key: getattr(requirements, key)
            for key in ("preserve_color", "preserve_audio")
            if getattr(requirements, key) is not None
        }
        render_policy = current_policy.model_copy(update=requested_policy)
        source_durations = source_duration_limits(project, candidates)
        originals = [item for item in candidates if item["origin"] == "user_upload"]
        primary_source_ids = sorted({item["source_id"] for item in originals})
        if not primary_source_ids:
            raise AgentInputError("剪辑任务缺少用户原片，请重新上传", code="source_required")
        initial_edit = not any(track.segments for track in project.timeline.tracks)
        requested_ratio = requested_aspect_ratio(state.get("query_text", ""))
        aspect_ratio = (
            intent.get("aspect_ratio", "16:9")
            if initial_edit
            else requested_ratio
            or choose_aspect_ratio("", project.canvas.width, project.canvas.height)
        )
        canvas = (
            _canvas_for_ratio(aspect_ratio, project.canvas)
            if initial_edit or requested_ratio
            else project.canvas
        )
        video_source_policy: dict[str, Any] = {
            "primary_source_ids": primary_source_ids,
            "external_material_ratio_limit": external_ratio_limit,
        }
        source_range = (
            explicit_source_range(state.get("query_text", ""))
            if len(primary_source_ids) == 1
            else None
        )
        if source_range:
            video_source_policy["requested_primary_source_range"] = {
                "source_id": primary_source_ids[0],
                "source_start_us": source_range[0],
                "source_end_us": source_range[1],
            }
        duration = intent.get("target_duration_seconds")
        if isinstance(duration, (int, float)) and math.isfinite(duration) and duration > 0:
            duration_us = round(duration * 1_000_000)
            video_source_policy.update(
                target_duration_us=duration_us,
                max_library_video_us_at_target=int(duration_us * external_ratio_limit),
            )
        compact_revision = not initial_edit and intent.get("operation_scope") == "refine"
        allow_video_transform = intent.get("video_transform_requested") is not False
        structural_revision = intent.get("operation_scope") == "structural"
        proposal_type: type[CommandProposal] = (
            InitialCommandProposal
            if initial_edit
            else RevisionCommandProposal
            if compact_revision
            else CommandProposal
        )
        response_schema = (
            refinement_proposal_schema(
                project, RevisionCommandProposal, allow_video_transform=allow_video_transform
            )
            if compact_revision
            else command_proposal_schema(proposal_type, allow_video_transform=allow_video_transform)
        )
        command_types = response_schema["properties"]["commands"]["items"]["discriminator"][
            "mapping"
        ]
        payload: dict[str, Any] = {
            "instruction": state.get("query_text", ""),
            "project": planning_snapshot(project),
            "materials": candidates,
            "contract": {
                "time_unit": "integer microseconds",
                "output_aspect_ratio": aspect_ratio,
                "video_source_policy": video_source_policy,
                "allowed_commands": list(command_types),
                "forbidden": ["RFC6902", "complete timeline replacement", "FFmpeg", "frames"],
                "response_schema": prompt_schema(response_schema),
                "delivery_constraints": requirements.model_dump(),
            },
        }
        system = (
            "You are CamCat's domain-command planning node. Return strict JSON with a commands "
            "array matching the supplied command contract. Use only stable source_id values from "
            "materials, integer microseconds, and stable anchors; never return array indexes, "
            "patch "
            "paths, a whole replacement timeline, frames, or renderer instructions. "
            "Build the main video from video_source_policy.primary_source_ids. For an empty "
            "timeline, use add_track with the initial uploaded-video segments in its segments "
            "array. Reference images guide optional licensed-library cutaways. Trim them to "
            "max_library_video_us_at_target and keep the library fraction of the actual video "
            "at or below external_material_ratio_limit. Keep the uploaded video if no library "
            "clip fits the budget. Use target_duration_us as the requested length when supplied. "
            "Use enough ranges from the uploaded video to reach that length within the budget."
        )
        system += (
            " Follow response_schema exactly, including each command, track and segment type tag. "
            "Use timeline_start_us for placement. The original media range is source_start_us "
            "and source_duration_us. Omit optional default fields."
            " Video transform x/y are translation offsets: centered placement is x=0,y=0, "
            "scale_x=1,scale_y=1. x=0.5,y=0.5 shifts by half the canvas and is not centered. "
            " Commands are applied in order to the supplied project. Add every new video, audio "
            "or text track before referencing it. Update or remove only existing tracks and "
            "segments. Use supplied source IDs only; do not invent music sources. "
            "If validation_error is supplied, return a corrected replacement commands array; "
            "the previous invalid proposal was not applied."
            " Transitions connect consecutive video segments on the same track, in forward "
            "timeline order, and are strictly shorter than both segments. A single segment "
            "has no transition. Remove affected transitions before reordering or deleting clips. "
            "repair_context reports the last valid simulated prefix and legal transition pairs; "
            "use it to correct the full proposal against the original project."
            " Check video_duration_us_by_origin when correcting the source mix. "
            "CamCat generates subtitles from source speech. "
            "Keep commands minimal for the requested edit. Preserve the audio for visual or "
            "subtitle edits. Add new music or sound effects when requested, and put their "
            "segments directly in add_track if the audio track is new. For non-looping audio use "
            "equal source_duration_us and timeline_duration_us, within the measured source window. "
            "For a repeating music/ambient bed whose source excerpt is shorter than the output, "
            "set loop=true explicitly. Audio fade_in_us + fade_out_us must not exceed "
            "timeline_duration_us. CamCat derives video timeline duration from the original "
            "media range and speed. All time fields use integer microseconds. "
            "Every audio and text segment must end within the visible video timeline. "
            "Use repair_context.visible_timeline_end_us to trim tracks to the actual video end."
            " SplitSegment.at_source_us is an ABSOLUTE position in the original source media. "
            "For a midpoint split use repair_context.tracks[].segments[].midpoint_source_us "
            "exactly. A timeline offset from the clip start maps to source_start_us plus "
            "offset_us times speed. A split preserves the clip's total duration."
            " TrimSegment.source_start_us is also ABSOLUTE in the original source, never "
            "relative to the current clip. If requested_primary_source_range is supplied, "
            "the uploaded video's source ranges must cover that exact interval in order; "
            "do not keep the old source_start_us merely to shorten the timeline. Preserve "
            "the project canvas and unrelated settings unless the user requests a change."
            " For requested storyboard slots, match every slot's subject direction, camera "
            "angle and motion against the material's shot evidence. Similar settings alone "
            "are insufficient. Record each selected clip's matching evidence in its reason. "
            "Do not infer a match from filenames or invent unsupported camera movement."
            " Honor delivery_constraints: forbidden transitions or subtitles must be absent. "
            "For full_source_clips select the complete file range source_start_us=0 and the "
            "listed material source_duration_us, speed=1. analyzed_windows are evidence, "
            "not a restriction to crop the file. Obey clip_count and use_all_sources when set. "
            "Visual descriptions tagged whole_media describe the whole original, not an exact "
            "window. Use filename to match the user's references to uploaded files. "
            "A dissolve additionally needs unused source frames before the incoming clip and "
            "after the outgoing clip; a clip starting at source time zero has no incoming handle."
        )
        if not initial_edit:
            payload["repair_context"] = _planning_repair_context(project, [])
            system += (
                " Revise existing subtitles with update_text_segment. Compare proposed values "
                "with the existing segment: copying its original values has no effect. "
                "To shorten subtitles, rewrite their actual text more concisely while keeping "
                "the meaning and language. Keep each caption in its ORIGINAL language even "
                "when the editing instruction uses a different language; an English caption "
                "stays English under Chinese editing instructions. Translate only when the "
                "user explicitly requests translation. Reducing display duration alone does not "
                "shorten "
                "the words. For tighter pacing, edit the existing video timing while retaining "
                "the requested source coverage. A title or goal change does not implement a "
                "requested video or subtitle edit. When changing video timing, revise the "
                "affected audio and text ranges to fit the new visible video. Preserve valid "
                "requested video/caption changes while repairing their dependent durations."
            )
        if compact_revision:
            system += (
                " Start with the compact "
                "existing-segment contract and retain the current tracks and sources. For a "
                "requested insertion, new track, reordering, split, deletion, source replacement "
                "or other operation outside the supplied contract, return requires_full_plan=true "
                "and commands=[] to request the full native contract. For compact edits return "
                "requires_full_plan=false with only the necessary existing-segment commands."
            )
        declared_slots = (
            storyboard_slots(state.get("query_text", ""))
            if initial_edit and getattr(self.llm, "source_evidence_enabled", False)
            else []
        )
        explicit_original_storyboard = (
            bool(declared_slots)
            and explicit_music_policy(state.get("query_text", "")) == "forbid"
            and intent.get("video_transform_requested") is not True
        )
        explicit_original_narrative = (
            self.narrative is not None
            and requirements.use_all_sources
            and narrative_ordering_requested(state.get("query_text", ""))
            and explicit_music_policy(state.get("query_text", "")) == "forbid"
            and intent.get("video_transform_requested") is not True
        )
        full_original_sequence = (
            initial_edit
            and (
                intent.get("planning_mode") == "source_sequence"
                or explicit_original_storyboard
                or explicit_original_narrative
            )
            and requirements.full_source_clips
            and requirements.transitions == "forbid"
            and requirements.subtitles == "forbid"
            and external_ratio_limit == 0
            and source_range is None
            and all(source_durations.get(source_id, 0) > 0 for source_id in primary_source_ids)
        )
        sequence_count = (
            requirements.clip_count
            if requirements.clip_count is not None
            else len(primary_source_ids)
            if requirements.use_all_sources
            else None
        )
        grounded_sequence = full_original_sequence and bool(
            getattr(self.llm, "source_evidence_enabled", False)
        )
        slots = declared_slots if grounded_sequence else []
        native_narrative = (
            grounded_sequence
            and not slots
            and self.narrative is not None
            and requirements.use_all_sources
            and narrative_ordering_requested(state.get("query_text", ""))
        )
        if slots:
            if sequence_count is not None and sequence_count != len(slots):
                raise AgentInputError(
                    "The explicit clip count differs from the storyboard slot count.",
                    code="storyboard_invalid",
                )
            sequence_count = len(slots)
        if full_original_sequence:
            response_schema = SourceSequenceProposal.schema_for_sources(
                primary_source_ids, sequence_count, source_filenames(project)
            )
            payload = sequence_payload(
                state.get("query_text", ""),
                originals,
                count=sequence_count,
                use_all_sources=requirements.use_all_sources,
            )
            system = SEQUENCE_SYSTEM
            if grounded_sequence and not slots:
                payload["materials"] = evidence_materials(payload["materials"])
                response_schema = EvidenceSequenceProposal.schema_for_materials(
                    payload["materials"], sequence_count
                )
                system = EVIDENCE_SEQUENCE_SYSTEM
        repair_primary_clip = False
        planning_recovery = None
        caption_corrections: dict[str, str] = {}
        caption_protection = (
            protected_caption_terms(self.llm, project)
            if not initial_edit and intent.get("caption_operation") == "shorten"
            else {}
        )
        if caption_protection:
            payload["protected_caption_terms"] = caption_protection
            system += (
                " Retain EVERY protected_caption_terms entry as a whole name or numeric fact "
                "in its caption. Shorten surrounding phrasing instead."
            )
        narrative_selection: SourceSequenceProposal | None = None
        for attempt in range(3):
            if native_narrative:
                assert self.narrative is not None
                if narrative_selection is None:
                    narrative_selection = self.narrative.plan(
                        project=project,
                        instruction=state.get("query_text", ""),
                        originals=originals,
                        count=sequence_count,
                        use_all_sources=requirements.use_all_sources,
                    )
                result = narrative_selection.model_dump(mode="json")
                planning_recovery = "native_multimodal_narrative"
            elif slots:
                if self.narrative is not None:
                    if narrative_selection is None:
                        narrative_selection = self.narrative.plan_storyboard(
                            project=project, slots=slots, originals=originals
                        )
                    result = narrative_selection.model_dump(mode="json")
                    planning_recovery = "native_multimodal_storyboard"
                else:
                    result = select_storyboard(
                        self.llm, self.retrieval.reranker, slots, payload["materials"]
                    ).model_dump(mode="json")
            elif repair_primary_clip:
                result = self.llm.json_completion(
                    system=(
                        "Choose the uploaded footage and source range for CamCat's main story. "
                        "Return source_id, source_start_us and source_duration_us from the "
                        "available footage in integer microseconds. Use the requested duration "
                        "when supplied."
                    ),
                    user=json.dumps(
                        {
                            "instruction": payload["instruction"],
                            "materials": originals,
                            "video_source_policy": video_source_policy,
                            "validation_error": payload["validation_error"],
                            "repair_context": payload["repair_context"],
                        },
                        ensure_ascii=False,
                    ),
                    response_schema=PrimaryClipProposal.schema_for_sources(primary_source_ids),
                )
            else:
                result = self.llm.json_completion(
                    system=system,
                    user=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    response_schema=response_schema,
                )
            commands: list[DomainEditCommand] | None = None
            source_set_verified = False
            try:
                if proposal_type is RevisionCommandProposal:
                    revision = RevisionCommandProposal.model_validate(
                        {
                            "commands": result.get("commands"),
                            "requires_full_plan": result.get("requires_full_plan", False),
                        }
                    )
                    if revision.requires_full_plan:
                        structural_revision = True
                        proposal_type = CommandProposal
                        response_schema = command_proposal_schema(
                            proposal_type, allow_video_transform=allow_video_transform
                        )
                        payload["contract"]["response_schema"] = prompt_schema(response_schema)
                        payload["contract"]["allowed_commands"] = list(
                            response_schema["properties"]["commands"]["items"]["discriminator"][
                                "mapping"
                            ]
                        )
                        system += (
                            " The full native contract is now available. Return its commands "
                            "directly and omit the compact routing flag requires_full_plan."
                        )
                        result = self.llm.json_completion(
                            system=system,
                            user=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                            response_schema=response_schema,
                        )
                parsed: list[DomainEditCommand]
                if repair_primary_clip:
                    choice = PrimaryClipProposal.model_validate(result)
                    parsed = [
                        _primary_clip_command(
                            project,
                            choice,
                            originals,
                            video_source_policy.get("target_duration_us"),
                        )
                    ]
                elif full_original_sequence:
                    selection = (
                        resolve_evidence(
                            EvidenceSequenceProposal.model_validate(result), payload["materials"]
                        )
                        if grounded_sequence and not slots and not native_narrative
                        else SourceSequenceProposal.model_validate(result)
                    )
                    parsed = sequence_commands(
                        selection,
                        project,
                        source_durations,
                        set(primary_source_ids),
                        count=sequence_count,
                        use_all_sources=requirements.use_all_sources,
                    )
                else:
                    proposal = proposal_type.model_validate({"commands": result.get("commands")})
                    parsed = list(proposal.commands)
                if not allow_video_transform:
                    parsed = [c for c in parsed if not isinstance(c, SetSegmentTransform)]
                    if initial_edit:
                        for index, command in enumerate(parsed):
                            if isinstance(command, AddTrack) and isinstance(
                                command.track, VideoTrack
                            ):
                                track = command.track.model_copy(
                                    update={
                                        "segments": [
                                            s.model_copy(update={"transform": Transform()})
                                            for s in command.track.segments
                                        ]
                                    }
                                )
                                parsed[index] = command.model_copy(update={"track": track})
                            elif isinstance(command, InsertSegment) and isinstance(
                                command.segment, VideoSegment
                            ):
                                parsed[index] = command.model_copy(
                                    update={
                                        "segment": command.segment.model_copy(
                                            update={"transform": Transform()}
                                        )
                                    }
                                )
                if caption_corrections:
                    updated_ids = set()
                    for index, command in enumerate(parsed):
                        if (
                            isinstance(command, UpdateTextSegment)
                            and command.segment_id in caption_corrections
                        ):
                            parsed[index] = command.model_copy(
                                update={"text": caption_corrections[command.segment_id]}
                            )
                            updated_ids.add(command.segment_id)
                    parsed.extend(
                        UpdateTextSegment(segment_id=identifier, text=text)
                        for identifier, text in caption_corrections.items()
                        if identifier not in updated_ids
                    )
                referenced = _referenced_source_ids(parsed)
                existing_sources = {item.source_id for item in project.sources}
                registrations = [
                    RegisterSource(source=registry[source_id])
                    for source_id in sorted(referenced - existing_sources)
                    if source_id in registry
                ]
                if referenced - existing_sources - set(registry):
                    raise ValueError(
                        "domain commands reference a source outside the supplied material set"
                    )
                source_set_verified = True
                commands = [
                    *(
                        [SetRenderPolicy(policy=render_policy)]
                        if render_policy != current_policy
                        else []
                    ),
                    *(
                        [UpdateCanvas(canvas=canvas)]
                        if initial_edit or canvas != project.canvas
                        else []
                    ),
                    *registrations,
                    *parsed,
                ]
                reduced = reduce_commands(project, commands)
                if not initial_edit:
                    linked_audio = linked_background_audio_commands(project, reduced.project)
                    if linked_audio:
                        commands.extend(linked_audio)
                        reduced = reduce_commands(project, commands)
                        planning_recovery = "linked_background_audio_after_video_trim"
                validate_delivery(
                    reduced.project, requirements, source_durations, set(primary_source_ids)
                )
                TimelineCompilerV2.validate_source_ranges(reduced.project, source_durations)
                self._validate_external_ratio(reduced.project, external_ratio_limit)
                if source_range:
                    _validate_requested_source_range(
                        reduced.project, primary_source_ids[0], source_range
                    )
                after_captions = _source_captions(reduced.project)
                pending_captions = {
                    caption.segment_id
                    for key, caption in _source_captions(project).items()
                    if caption != after_captions.get(key)
                }
                _validate_visible_bounds(reduced.project, pending_text_ids=pending_captions)
                _validate_caption_revision(
                    project,
                    reduced.project,
                    intent.get("caption_language"),
                    str(intent.get("caption_operation") or "keep"),
                    caption_protection,
                )
            except (ValidationError, ValueError) as exc:
                if isinstance(exc, RequestedSourceRangeError):
                    planning_recovery = "source_range_after_plan_validation_failure"
                feedback = _validation_feedback(exc)
                if str(exc) == "commands did not change the project":
                    feedback += (
                        "; every proposed value equals the original project. Compare the "
                        "previous_output to project and repair_context, then change the "
                        "requested timing or caption content. Do not repeat the same values "
                        "or change unrelated titles/goals to create a version."
                    )
                context = _planning_repair_context(project, commands or [])
                logging.getLogger(__name__).warning(
                    "planner_validation_failed %s",
                    json.dumps(
                        {
                            "attempt": attempt + 1,
                            "error": feedback,
                            "context": {
                                **context,
                                "tracks": [
                                    {
                                        **track,
                                        "segments": [
                                            {
                                                key: value
                                                for key, value in item.items()
                                                if key != "text"
                                            }
                                            for item in track["segments"]
                                        ],
                                    }
                                    for track in context["tracks"]
                                ],
                            },
                        },
                        ensure_ascii=False,
                    ),
                )
                video_usage = context["video_duration_us_by_origin"]
                library_over_budget = video_usage["licensed_library"] > (
                    external_ratio_limit + 1e-9
                ) * sum(video_usage.values())
                primary_clip_error = (
                    video_usage["user_upload"] == 0
                    or library_over_budget
                    or isinstance(exc, VisibleTimelineError)
                )
                last_primary_recovery = (
                    attempt == 1
                    and initial_edit
                    and source_set_verified
                    and not repair_primary_clip
                    and not requirements.full_source_clips
                    and not requirements.use_all_sources
                    and requirements.clip_count is None
                    and (
                        primary_clip_error or context.get("failed_command_type") == "set_transition"
                    )
                )
                caption_ids = (
                    {
                        command.segment_id
                        for command in parsed
                        if isinstance(command, UpdateTextSegment)
                    }
                    if commands is not None
                    else set()
                )
                last_caption_recovery = (
                    attempt == 1
                    and not initial_edit
                    and isinstance(exc, CaptionRewriteError)
                    and bool(caption_ids)
                    and not caption_corrections
                )
                if attempt and not (last_primary_recovery or last_caption_recovery):
                    raise EditPlanRejected(feedback) from exc
                if (
                    not initial_edit
                    and caption_ids
                    and not caption_corrections
                    and (
                        isinstance(exc, CaptionRewriteError)
                        or (
                            str(exc) == "commands did not change the project"
                            and intent.get("caption_operation") in {"shorten", "rewrite"}
                        )
                    )
                ):
                    try:
                        caption_corrections = repair_caption_text(
                            self.llm,
                            project,
                            caption_ids,
                            str(state.get("query_text", "")),
                            operation=str(intent.get("caption_operation") or "rewrite"),
                            requested_language=intent.get("caption_language"),
                            protected_terms=caption_protection,
                        )
                    except (ValidationError, ValueError) as repair_error:
                        raise EditPlanRejected(_validation_feedback(repair_error)) from repair_error
                    payload["caption_text_repairs"] = caption_corrections
                payload["validation_error"] = feedback
                payload["previous_output"] = result
                payload["repair_context"] = (
                    {
                        "allowed_material_ids": list(
                            material_aliases(primary_source_ids, source_filenames(project))
                        ),
                        "clip_count": sequence_count,
                        "use_all_sources": requirements.use_all_sources,
                    }
                    if full_original_sequence
                    else context
                )
                if last_primary_recovery:
                    planning_recovery = "primary_after_plan_validation_failure"
                repair_primary_clip = (
                    initial_edit
                    and source_set_verified
                    and (primary_clip_error or last_primary_recovery)
                )
                if not initial_edit and not structural_revision:
                    proposal_type = RefinementCommandProposal
                    response_schema = refinement_proposal_schema(
                        project, allow_video_transform=allow_video_transform
                    )
                    payload["repair_context"] = _planning_repair_context(project, [])
                    payload["contract"]["response_schema"] = prompt_schema(response_schema)
                    payload["contract"]["allowed_commands"] = list(
                        response_schema["properties"]["commands"]["items"]["discriminator"][
                            "mapping"
                        ]
                    )
                    system += (
                        " Repair this revision without deleting the existing uploaded video. "
                        "Retain its tracks and sources. Use the schema's existing segment IDs "
                        "for trimming, audio adjustments and caption edits. Return delta edits "
                        "rather than recreating tracks or clips."
                    )
                continue
            return {
                "domain_commands": [item.model_dump(mode="json") for item in commands],
                "simulated_document": reduced.project.model_dump(mode="json", by_alias=True),
                "final_answer": str(result.get("summary", "剪辑命令已生成。")),
                "planning_recovery": planning_recovery,
            }
        raise AssertionError("unreachable")

    def generate_subtitles(self, state: CamCatState) -> dict[str, Any]:
        original = EditingProjectV2.model_validate(state["current_document"])
        project = EditingProjectV2.model_validate(state["simulated_document"])
        commands = _parse_commands(state["domain_commands"])
        requirements = delivery_constraints(
            state.get("query_text", ""), state.get("intent", {}).get("delivery_constraints")
        )
        if requirements.subtitles == "forbid":
            return {
                "domain_commands": state["domain_commands"],
                "simulated_document": project.model_dump(mode="json", by_alias=True),
                "final_answer": state.get("final_answer", "") + " 按要求保留无字幕时间线。",
            }
        text_tracks = [item for item in project.timeline.tracks if isinstance(item, TextTrack)]
        track = (
            text_tracks[0] if text_tracks else TextTrack(track_id="text-captions", name="Captions")
        )
        existing = {item.segment_id: item for item in track.segments}
        original_ids = {
            item.segment_id
            for old_track in original.timeline.tracks
            if isinstance(old_track, TextTrack)
            for item in old_track.segments
        }
        captions = _source_captions(project)
        for key, before in _source_captions(original).items():
            edited = existing.get(before.segment_id)
            if edited is None:
                if before.segment_id in original_ids:
                    captions.pop(key, None)
                continue
            after = captions.get(key)
            if before == after:
                captions.pop(key)
                continue
            commands.append(RemoveSegment(track_id=track.track_id, segment_id=edited.segment_id))
            del existing[edited.segment_id]
            if after is not None:
                captions[key] = after.model_copy(
                    update={"text": edited.text, "style": edited.style}
                )

        added = 0
        for caption in captions.values():
            if caption.segment_id in existing or any(
                caption.start_us < item.start_us + item.duration_us
                and item.start_us < caption.start_us + caption.duration_us
                for item in existing.values()
            ):
                continue
            if not text_tracks and not added:
                commands.append(AddTrack(track=track))
            commands.append(InsertSegment(track_id=track.track_id, segment=caption))
            existing[caption.segment_id] = caption
            added += 1
        reduced = reduce_commands(original, commands)
        return {
            "domain_commands": [item.model_dump(mode="json") for item in commands],
            "simulated_document": reduced.project.model_dump(mode="json", by_alias=True),
            "final_answer": f"{state.get('final_answer', '')} 已生成 {added} 条字幕。",
        }

    def validate_project(self, state: CamCatState) -> dict[str, Any]:
        commands = _parse_commands(state["domain_commands"])
        project = EditingProjectV2.model_validate(state["current_document"])
        reduced = reduce_commands(project, commands)
        expected = EditingProjectV2.model_validate(state["simulated_document"])
        if reduced.project != expected:
            raise ValueError("command simulation is nondeterministic")
        _validate_visible_bounds(reduced.project)
        candidates, _ = self._candidate_registry(state, reduced.project)
        durations = source_duration_limits(reduced.project, candidates)
        requirements = delivery_constraints(
            state.get("query_text", ""), state.get("intent", {}).get("delivery_constraints")
        )
        primary = {s.source_id for s in reduced.project.sources if s.origin == "user_upload"}
        validate_delivery(reduced.project, requirements, durations, primary)
        TimelineCompilerV2.validate_source_ranges(reduced.project, durations)
        return {"simulated_document": reduced.project.model_dump(mode="json", by_alias=True)}

    def persist(self, state: CamCatState) -> dict[str, Any]:
        if state.get("mode") != "edit":
            return {}
        version, document = self.persistence.persist(
            session_id=state["session_id"],
            owner_id=state["owner_id"],
            base_version=int(state["base_version"]),
            commands=_parse_commands(state["domain_commands"]),
            reason=state.get("persistence_reason") or state.get("query_text", "agent edit"),
        )
        return {"persisted_version": version, "persisted_document": document}

    @staticmethod
    def _candidate_registry(
        state: CamCatState, project: EditingProjectV2
    ) -> tuple[list[dict[str, Any]], dict[str, MediaSourceRef]]:
        existing = {item.source_id: item for item in project.sources}
        candidates: list[dict[str, Any]] = []
        registry = dict(existing)
        for item in state.get("source_materials", []):
            source_id = str(item["media_id"])
            source = existing[source_id]
            candidates.append(
                {
                    "source_id": source_id,
                    "origin": "user_upload",
                    "media_type": "video",
                    "source_start_us": round(float(item["start_time"]) * 1_000_000),
                    "source_duration_us": round(
                        (float(item["end_time"]) - float(item["start_time"])) * 1_000_000
                    ),
                    "description": item.get("description_text", ""),
                    "transcript": item.get("transcript", ""),
                    "quality_score": item.get("quality_score", 0.5),
                }
            )
        for item in state.get("ranked_materials", []):
            entity = item["entity"]
            source_id = str(item["segment_id"])
            duration_us = round(
                (float(entity["end_time"]) - float(entity["start_time"])) * 1_000_000
            )
            source = MediaSourceRef(
                source_id=source_id,
                origin="licensed_library",
                storage_key=str(entity["storage_key"]),
                retention_class="library",
                metadata={
                    "duration_us": duration_us,
                    "license_name": entity.get("license_name"),
                    "source_url": entity.get("source_url"),
                },
            )
            registry[source_id] = source
            candidates.append(
                {
                    "source_id": source_id,
                    "origin": "licensed_library",
                    "media_type": "video",
                    "source_start_us": 0,
                    "source_duration_us": duration_us,
                    "description": entity.get("description_text", ""),
                    "reranker_score": item["reranker_score"],
                }
            )
        for item in cast(list[dict[str, Any]], project.metadata.get("audio_library", [])):
            source_id = str(item["id"])
            audio_duration_us = item.get("duration_us")
            source_range = {"source_start_us": 0}
            measured: dict[str, Any] = {}
            if isinstance(audio_duration_us, int) and audio_duration_us > 0:
                source_range["source_duration_us"] = audio_duration_us
                measured["duration_us"] = audio_duration_us
            source = MediaSourceRef(
                source_id=source_id,
                origin="licensed_library",
                storage_key=str(item["storage_key"]),
                retention_class="library",
                metadata={
                    "license_name": item["license_name"],
                    "source_url": item["source_url"],
                    "media_type": "audio",
                    "kind": item["kind"],
                    **measured,
                },
            )
            registry.setdefault(source_id, source)
            candidates.append(
                {
                    "source_id": source_id,
                    "origin": "licensed_library",
                    "media_type": "audio",
                    "role": item["kind"],
                    "description": item.get("title", ""),
                    "tags": item.get("tags", []),
                    **source_range,
                }
            )
        return candidates, registry

    @staticmethod
    def _validate_external_ratio(project: EditingProjectV2, limit: float) -> None:
        duration_by_origin = _video_duration_by_origin(project)
        if not duration_by_origin["user_upload"]:
            raise ValueError("user footage must remain the primary story")
        if duration_by_origin["licensed_library"] / sum(duration_by_origin.values()) > limit + 1e-9:
            raise ValueError("licensed-library duration exceeds the requested ratio limit")

    @staticmethod
    def _traced(name: str, function: Any) -> Any:
        def wrapped(state: CamCatState) -> dict[str, Any]:
            started = time.perf_counter()
            with capture_provider_calls() as calls:
                try:
                    result = function(state)
                    return {
                        **result,
                        "node_trace": [
                            *state.get("node_trace", []),
                            {
                                "node_name": name,
                                "status": "completed",
                                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                                "provider_calls": calls,
                            },
                        ],
                    }
                except Exception as error:
                    trace = [
                        *state.get("node_trace", []),
                        {
                            "node_name": name,
                            "status": "failed",
                            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                            "provider_calls": calls,
                        },
                    ]
                    attach_failure_trace(error, trace)
                    raise

        return wrapped


def _primary_clip_command(
    project: EditingProjectV2,
    choice: PrimaryClipProposal,
    originals: list[dict[str, Any]],
    target_duration_us: int | None,
) -> AddTrack | InsertSegment:
    ranges = [item for item in originals if item["source_id"] == choice.source_id]
    if not ranges:
        raise ValueError("primary clip must select an uploaded source")
    start_us = min(item["source_start_us"] for item in ranges)
    end_us = max(item["source_start_us"] + item["source_duration_us"] for item in ranges)
    duration_us = min(target_duration_us or choice.source_duration_us, end_us - start_us)
    segment = VideoSegment(
        segment_id=f"primary-{project.project_id}",
        source_id=choice.source_id,
        timeline_start_us=0,
        timeline_duration_us=duration_us,
        source_start_us=max(start_us, min(choice.source_start_us, end_us - duration_us)),
        source_duration_us=duration_us,
    )
    for track in project.timeline.tracks:
        if isinstance(track, VideoTrack):
            return InsertSegment(track_id=track.track_id, segment=segment)
    return AddTrack(
        track=VideoTrack(track_id=f"video-{project.project_id}", name="主视频", segments=[segment])
    )


def _validate_caption_revision(
    before: EditingProjectV2,
    after: EditingProjectV2,
    requested_language: str | None,
    operation: str,
    protected_terms: dict[str, list[str]] | None = None,
) -> None:
    """Validate the observed script error and meaningful wording reduction."""
    originals = {
        segment.segment_id: segment.text
        for track in before.timeline.tracks
        if isinstance(track, TextTrack)
        for segment in track.segments
    }
    shortened = False
    for track in after.timeline.tracks:
        if not isinstance(track, TextTrack):
            continue
        for segment in track.segments:
            old = originals.get(segment.segment_id, "")
            if not retains_caption_terms(
                segment.text, (protected_terms or {}).get(segment.segment_id, [])
            ):
                raise CaptionRewriteError(
                    f"caption {segment.segment_id} omitted a protected original "
                    "name or numeric fact"
                )
            shortened |= bool(old) and sum(character.isalnum() for character in segment.text) < sum(
                character.isalnum() for character in old
            )
            if operation == "keep" and segment.text == old:
                continue
            if (
                requested_language is None
                and old.isascii()
                and any(character.isalpha() for character in old)
                or bool(requested_language and requested_language.startswith("en"))
            ) and any("\u3400" <= character <= "\u9fff" for character in segment.text):
                raise CaptionRewriteError(
                    f"caption {segment.segment_id} introduced Chinese text while rewriting an "
                    "ASCII caption without a requested target language. Use the original "
                    "text from project and shorten its original-language words instead of "
                    "translating."
                )
    if operation == "shorten" and originals and not shortened:
        raise CaptionRewriteError(
            "Caption wording was not shortened. Reduce its letters/words in the original "
            "language; deleting punctuation or changing display duration alone is insufficient."
        )


def _video_duration_by_origin(project: EditingProjectV2) -> dict[str, int]:
    origins = {item.source_id: item.origin for item in project.sources}
    durations = {"user_upload": 0, "licensed_library": 0}
    for track in project.timeline.tracks:
        if isinstance(track, VideoTrack):
            for segment in track.segments:
                origin = origins[segment.source_id]
                durations[origin] = durations.get(origin, 0) + segment.timeline_duration_us
    return durations


def _validate_visible_bounds(
    project: EditingProjectV2, *, pending_text_ids: set[str] | None = None
) -> None:
    fps_num, fps_den = project.canvas.fps_num, project.canvas.fps_den
    frames = [
        sum(
            us_range_to_frames(
                segment.timeline_start_us, segment.timeline_duration_us, fps_num, fps_den
            )
        )
        for track in project.timeline.tracks
        if isinstance(track, VideoTrack)
        for segment in track.segments
    ]
    if not frames:
        return
    frame_count = max(frames)
    for track in project.timeline.tracks:
        if isinstance(track, AudioTrack):
            for audio in track.segments:
                if (
                    sum(
                        us_range_to_frames(
                            audio.timeline_start_us, audio.timeline_duration_us, fps_num, fps_den
                        )
                    )
                    > frame_count
                ):
                    raise VisibleTimelineError("audio segment exceeds the visible timeline")
        elif isinstance(track, TextTrack):
            for text in track.segments:
                if pending_text_ids and text.segment_id in pending_text_ids:
                    continue
                if (
                    sum(us_range_to_frames(text.start_us, text.duration_us, fps_num, fps_den))
                    > frame_count
                ):
                    raise VisibleTimelineError("text segment exceeds the visible timeline")


def _planning_repair_context(
    project: EditingProjectV2, commands: list[DomainEditCommand]
) -> dict[str, Any]:
    current = project
    failed: dict[str, Any] = {}
    for index, command in enumerate(commands):
        try:
            current = reduce_commands(project, commands[: index + 1]).project
        except ValueError as error:
            if str(error) == "commands did not change the project":
                continue
            failed = {"failed_command_index": index, "failed_command_type": command.type}
            break
    tracks = []
    pairs = []
    for track in current.timeline.tracks:
        segments = [
            {
                "segment_id": item.segment_id,
                "start_us": item.start_us
                if isinstance(item, TextSegment)
                else item.timeline_start_us,
                "duration_us": item.duration_us
                if isinstance(item, TextSegment)
                else item.timeline_duration_us,
                **({"text": item.text} if isinstance(item, TextSegment) else {}),
                **(
                    {"source_id": item.source_id}
                    if isinstance(item, (VideoSegment, AudioSegment))
                    else {}
                ),
                **(
                    {
                        "source_start_us": item.source_start_us,
                        "source_duration_us": item.source_duration_us,
                        "speed": item.speed,
                        "midpoint_source_us": item.source_start_us + item.source_duration_us // 2,
                    }
                    if isinstance(item, VideoSegment)
                    else {}
                ),
            }
            for item in track.segments
        ]
        tracks.append({"track_id": track.track_id, "type": track.type, "segments": segments})
        if isinstance(track, VideoTrack):
            for left, right in zip(track.segments[:-1], track.segments[1:], strict=True):
                maximum = min(left.timeline_duration_us, right.timeline_duration_us) - 1
                if maximum > 0:
                    pairs.append(
                        {
                            "track_id": track.track_id,
                            "left_segment_id": left.segment_id,
                            "right_segment_id": right.segment_id,
                            "max_duration_us": maximum,
                        }
                    )
    return {
        **failed,
        "tracks": tracks,
        "allowed_transitions": pairs,
        "source_ids": [source.source_id for source in current.sources],
        "video_duration_us_by_origin": _video_duration_by_origin(current),
        "visible_timeline_end_us": max(
            (
                segment.timeline_start_us + segment.timeline_duration_us
                for track in current.timeline.tracks
                if isinstance(track, VideoTrack)
                for segment in track.segments
            ),
            default=0,
        ),
        "existing_transitions": [
            edge.model_dump(mode="json") for edge in current.timeline.transitions
        ],
    }


def _referenced_source_ids(commands: list[DomainEditCommand]) -> set[str]:
    referenced: set[str] = set()
    for command in commands:
        if isinstance(command, (InsertSegment, AddAudioSegment)):
            if isinstance(command.segment, (VideoSegment, AudioSegment)):
                referenced.add(command.segment.source_id)
        elif isinstance(command, AddTrack):
            referenced.update(
                segment.source_id
                for segment in command.track.segments
                if isinstance(segment, (VideoSegment, AudioSegment))
            )
        elif isinstance(command, ReplaceSegmentMedia):
            referenced.add(command.source_id)
    return referenced


def _validation_feedback(error: ValueError) -> str:
    if isinstance(error, ValidationError):
        messages = []
        for detail in error.errors(include_input=False, include_context=False, include_url=False):
            path = ".".join(str(part) for part in detail["loc"]) or "project"
            messages.append(f"{path}: {detail['msg']}")
        return "; ".join(messages).replace("\n", " ")
    return str(error).replace("\n", " ")


def _parse_commands(value: Any) -> list[DomainEditCommand]:
    if not isinstance(value, list) or not value:
        raise ValueError("domain command output must contain a nonempty commands array")
    return TypeAdapter(list[DomainEditCommand]).validate_python(value)


def _canvas_for_ratio(value: Any, current: Canvas) -> Canvas:
    dimensions = {
        "16:9": (1920, 1080),
        "9:16": (1080, 1920),
        "3:4": (1080, 1440),
        "4:3": (1440, 1080),
        "1:1": (1080, 1080),
    }
    width, height = dimensions.get(str(value), (current.width, current.height))
    if current.width * height == current.height * width:
        return current
    return Canvas(
        width=width,
        height=height,
        fps_num=current.fps_num,
        fps_den=current.fps_den,
    )


def _validate_requested_source_range(
    project: EditingProjectV2,
    source_id: str,
    requested: tuple[int, int],
) -> None:
    clips = sorted(
        (
            clip
            for track in project.timeline.tracks
            if isinstance(track, VideoTrack)
            for clip in track.segments
            if clip.source_id == source_id
        ),
        key=lambda clip: clip.timeline_start_us,
    )
    ranges = [
        (clip.source_start_us, clip.source_start_us + clip.source_duration_us) for clip in clips
    ]
    if (
        not ranges
        or ranges[0][0] != requested[0]
        or ranges[-1][1] != requested[1]
        or any(left[1] != right[0] for left, right in zip(ranges, ranges[1:], strict=False))
    ):
        raise RequestedSourceRangeError(
            f"requested source range for {source_id} is {requested} in absolute microseconds; "
            f"observed source intervals: {ranges}. Use the requested start and end, not the "
            "previous clip offset."
        )


__all__ = ["CamCatGraph", "CamCatState", "editing_retrieval_filters"]
