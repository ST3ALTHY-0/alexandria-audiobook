"""Walk 2f: Character description generation.

Generates concise descriptions for each character in a book by sending
sampled text excerpts (spans where the character is speaker, mentioned, or
present) to an LLM.  Descriptions are stored in book-scoped persona revisions.

For each character, the walk:
1. Queries all spans where the character is speaker, mentioned, or present.
2. Samples up to 5 representative spans spread across the book.
3. Sends character name, aliases, and sampled span texts to the LLM.
    4. Parses the response (description + confidence).
5. Stores the description in a book-scoped ``persona_revision`` row.

Confidence filter:
- ≥0.7: auto-accept (description stored)
- <0.5: auto-reject (description discarded)
- 0.5–0.7: flagged for user review (description stored but tracked)

LLM configuration is resolved via ``resolve_task_config('character_description', storage, book_id)``
with temperature=0.1 for format stability.
"""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from ._llm_helpers import chat_completion, extract_json_from_llm_response

if TYPE_CHECKING:
    from app.pipeline.adapter import PipelineStorage

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def execute(book_id: str, storage: PipelineStorage, config: dict[str, Any]) -> dict:
    """Run Walk 2f character description generation for a book.

    Queries characters for the book, samples spans for each character, sends
    them to an LLM for description generation, parses the response, and stores
    descriptions in book-scoped ``persona_revision`` rows.

    Parameters
    ----------
    book_id:
        UUID of the book to process.
    storage:
        Pipeline storage adapter.
    config:
        App config dict (kept for the runner contract; not consulted by ``resolve_task_config``).

    Returns
    -------
    dict:
        Summary with keys: ``book_id``, ``characters_processed``,
        ``descriptions_generated``, ``descriptions_for_review``, ``errors``.
    """
    from app.utils import create_llm_client, resolve_task_config

    result: dict[str, Any] = {
        "book_id": book_id,
        "characters_processed": 0,
        "descriptions_generated": 0,
        "descriptions_for_review": 0,
        "errors": [],
    }

    # Resolve LLM config for character description
    llm_config = resolve_task_config("character_description", storage, book_id)
    client, _ = create_llm_client(config_path=None)
    model_name = llm_config["model_name"]
    temperature = llm_config["temperature"]
    reasoning_effort = llm_config.get("reasoning_effort")
    prompt = llm_config.get("prompt")

    # Look up book existence
    book_rows = storage.execute_query(
        "SELECT series_id FROM book WHERE id = ?", (book_id,)
    )
    if not book_rows:
        logger.error(f"Book {book_id} not found")
        result["errors"].append({"book_id": book_id, "error": "Book not found"})
        return result

    # Load existing characters for this book (id, name, aliases)
    existing_characters = _load_existing_characters(book_id, storage)

    for char_row in existing_characters:
        character_id = char_row["id"]
        character_name = char_row["name"]
        character_aliases = char_row["aliases"]

        result["characters_processed"] += 1

        try:
            _process_character(
                book_id=book_id,
                character_id=character_id,
                character_name=character_name,
                character_aliases=character_aliases,
                storage=storage,
                client=client,
                model_name=model_name,
                temperature=temperature,
                reasoning_effort=reasoning_effort,
                system_prompt_override=prompt,
                result=result,
            )
        except Exception as e:  # noqa: BLE001 — per-item error isolation: any error must log, record, and continue
            logger.error(f"Error processing character {character_id}: {e}")
            result["errors"].append({"character_id": character_id, "error": str(e)})

    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _load_existing_characters(
    book_id: str, storage: PipelineStorage
) -> list[dict[str, Any]]:
    """Load existing character id, name, and aliases for this book."""
    rows = storage.execute_query(
        """
        SELECT c.id, c.name, c.aliases
        FROM character c
        JOIN character_book cb ON cb.character_id = c.id
        WHERE cb.book_id = ?
        ORDER BY c.name
        """,
        (book_id,),
    )
    return [
        {
            "id": row["id"],
            "name": row["name"],
            "aliases": row["aliases"] or "[]",
        }
        for row in rows
    ]


def _collect_character_spans(
    book_id: str, character_id: str, storage: PipelineStorage
) -> list[dict[str, str]]:
    """Collect all spans where the character is speaker, mentioned, or present.

    Returns a list of dicts with span_id, text, and relation_type.
    """
    rows = storage.execute_query(
        """
        SELECT s.id AS span_id, s.text, cs.relation_type
        FROM character_span cs
        JOIN span s ON cs.span_id = s.id
        JOIN paragraph_span ps ON ps.child_id = s.id
        JOIN scene_paragraph sp ON sp.child_id = ps.parent_id
        JOIN chapter_scene sc ON sc.child_id = sp.parent_id
        JOIN book_chapter bc ON bc.child_id = sc.parent_id
        WHERE cs.character_id = ?
          AND bc.parent_id = ?
          AND cs.relation_type IN ('speaker', 'mentioned', 'present')
        ORDER BY bc.position, sc.position, sp.position, ps.position, s.id
        """,
        (character_id, book_id),
    )
    return [
        {
            "span_id": row["span_id"],
            "text": row["text"] or "",
            "relation_type": row["relation_type"],
        }
        for row in rows
    ]


def _sample_spans(
    spans: list[dict[str, str]], max_samples: int = 5
) -> list[dict[str, str]]:
    """Sample up to max_samples spans spread across the book.

    Takes a spread from the collected spans to cover different parts of the book.
    """
    if len(spans) <= max_samples:
        return spans

    # Take evenly spaced samples
    step = len(spans) / max_samples
    sampled = []
    for i in range(max_samples):
        idx = int(i * step)
        sampled.append(spans[idx])
    return sampled


def _process_character(
    book_id: str,
    character_id: str,
    character_name: str,
    character_aliases: str,
    storage: PipelineStorage,
    client: Any,
    model_name: str,
    temperature: float,
    reasoning_effort: str | None,
    system_prompt_override: str | None,
    result: dict,
) -> None:
    """Process a single character: collect spans, call LLM, store description."""
    # Collect spans for this character
    spans = _collect_character_spans(book_id, character_id, storage)

    if not spans:
        logger.warning(
            f"No spans found for character {character_id} ({character_name})"
        )
        return

    # Sample up to 5 representative spans
    sampled_spans = _sample_spans(spans, max_samples=5)

    # Build LLM prompt
    prompt = _build_description_prompt(
        character_name=character_name,
        character_aliases=character_aliases,
        sampled_spans=sampled_spans,
    )

    # Call LLM
    response_text = chat_completion(
        client=client,
        model_name=model_name,
        temperature=temperature,
        reasoning_effort=reasoning_effort,
        system_prompt=(
            system_prompt_override
            or "You are a literary analyst specializing in character analysis and description."
        ),
        user_prompt=prompt,
    )

    # Parse response
    description_data = _parse_llm_response(response_text)

    if not description_data:
        logger.warning(f"Failed to parse description for character {character_id}")
        return

    description = description_data.get("description", "").strip()
    if not description:
        logger.warning(f"Empty description for character {character_id}")
        return

    confidence = description_data.get("confidence", 0.8)
    if not isinstance(confidence, (int, float)):
        confidence = 0.8
    # Clamp to [0, 1]
    confidence = max(0.0, min(1.0, float(confidence)))

    # Confidence filter
    if confidence < 0.5:
        # Auto-reject
        return

    is_review = 0.5 <= confidence < 0.7

    # Store description in a book-scoped persona revision.
    with storage.savepoint("walk_2f_character"):
        stored = _store_description(
            book_id,
            character_id,
            character_aliases,
            description,
            sampled_spans,
            "needs_review" if is_review else "accepted",
            storage,
        )

    if not stored:
        logger.info("Skipping protected persona for character %s", character_id)
        return

    result["descriptions_generated"] += 1

    if is_review:
        result["descriptions_for_review"] += 1


def _store_description(
    book_id: str,
    character_id: str,
    character_aliases: str,
    description: str,
    sampled_spans: list[dict[str, str]],
    review_state: str,
    storage: PipelineStorage,
) -> bool:
    """Store a generated description in a book-scoped persona revision.

    ``character_metadata`` is character-global and must not be overwritten by
    evidence collected for only one book.
    """
    previous = storage.execute_query(
        """SELECT persona_id, fields_json, protected FROM persona_revision
            WHERE character_id = ? AND book_id = ?
            ORDER BY revision DESC, created_ms DESC, persona_id DESC LIMIT 1""",
        (character_id, book_id),
    )
    previous_id = previous[0]["persona_id"] if previous else None
    if previous and previous[0]["protected"]:
        return False
    revision_rows = storage.execute_query(
        "SELECT COALESCE(MAX(revision), 0) AS revision FROM persona_revision "
        "WHERE character_id = ?",
        (character_id,),
    )
    revision = int(revision_rows[0]["revision"]) + 1
    persona_id = f"persona-{uuid4().hex}"
    fields = json.loads(previous[0]["fields_json"]) if previous else {}
    if not isinstance(fields, dict):
        fields = {}
    fields["identity"] = description
    storage.insert_persona_revision(
        {
            "persona_id": persona_id,
            "character_id": character_id,
            "book_id": book_id,
            "revision": revision,
            "fields_json": json.dumps(fields),
            "evidence_json": json.dumps(
                [{"anchor": span["span_id"]} for span in sampled_spans]
            ),
            "aliases_json": character_aliases,
            "scene_scope": "book",
            "review_state": review_state,
            "protected": 0,
            "voice_consequences_json": "{}",
            "author_id": "local",
            "created_ms": int(time.time() * 1000),
            "superseded_by": None,
        }
    )
    if previous_id:
        storage.supersede_persona_revision(previous_id, persona_id)
    return True


def _build_description_prompt(
    character_name: str,
    character_aliases: str,
    sampled_spans: list[dict[str, str]],
) -> str:
    """Build the LLM prompt for character description generation.

    Includes character name, aliases, and sampled span texts labeled with
    their relation_type (speaker/mentioned/present).
    """
    # Parse aliases JSON
    try:
        aliases_list = json.loads(character_aliases)
        if not isinstance(aliases_list, list):
            aliases_list = []
    except json.JSONDecodeError:
        aliases_list = []

    aliases_text = ", ".join(aliases_list) if aliases_list else "(none)"

    # Build span excerpts
    span_lines = []
    for idx, span in enumerate(sampled_spans, start=1):
        text = span.get("text", "").strip()
        relation_type = span.get("relation_type", "present")
        if text:
            span_lines.append(f"[Excerpt {idx}] ({relation_type}) {text}")

    spans_text = "\n".join(span_lines)

    prompt = f"""You are analyzing text excerpts from a novel to generate a concise character description.

Character name: {character_name}
Aliases: {aliases_text}

Here are representative text excerpts where this character appears:

{spans_text}

Generate a concise description of this character based on the provided text excerpts. Return a JSON object with:
- "description": a concise character description (string)
- "confidence": float between 0.0 and 1.0 indicating your confidence in this description

Return ONLY a JSON object, no other text.

Example:
{{"description": "John Smith is a stern but fair mentor figure who speaks with authority.", "confidence": 0.9}}
"""
    return prompt


def _parse_llm_response(response_text: str) -> dict:
    """Parse the LLM response into a description dict.

    Returns a dict with description and confidence. If parsing fails,
    returns empty dict.
    """
    description_data = extract_json_from_llm_response(
        response_text, expected_type="dict"
    )
    if description_data is None:
        logger.error(f"Failed to parse LLM response as JSON: {response_text[:200]}")
        return {}

    if not isinstance(description_data, dict):
        logger.error(f"LLM response is not a dict: {type(description_data)}")
        return {}

    # Extract and validate fields
    description = description_data.get("description")
    confidence = description_data.get("confidence", 0.8)

    if not isinstance(description, str) or not description.strip():
        logger.error(f"description is not a valid string: {description}")
        return {}

    return {
        "description": description.strip(),
        "confidence": confidence,
    }
