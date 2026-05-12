import asyncio
import random
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional

from command_handlers import MessageType
from plugins.markdown import markdown_to_image
from plugins.stickers import sticker_to_segment

# Matches both special tag types in one pass
_SPECIAL_RE   = re.compile(r'(<render_md>.*?</render_md>|<sticker:\w+>)', re.DOTALL)
_RENDER_MD_RE = re.compile(r'<render_md>(.*?)</render_md>', re.DOTALL)
_STICKER_RE   = re.compile(r'<sticker:(\w+)>')
_CQ_RE        = re.compile(r'\[CQ:[^\]]*\]')


MultimodalProcessor = Callable[[list, str], Awaitable[str]]


class AgentAction(Enum):
    IGNORE = "ignore"
    TOOL = "tool"
    CHAT = "chat"


@dataclass
class AgentDecision:
    action: AgentAction
    reason: str
    command_type: Optional[Any] = None


@dataclass
class AgentRunResult:
    handled: bool
    action: AgentAction
    reason: str


class AgentOrchestrator:
    """Coordinates command tools, persona prompts, sessions, and replies.

    This is deliberately small for now. It gives the bot a single agent entry
    point without forcing every plugin to become an LLM tool immediately.
    """

    def __init__(
        self,
        bot_interfaces: Dict[str, Any],
        command_handler,
        persona_engine,
        session_manager,
        multimodal_processor: MultimodalProcessor,
    ):
        self.bot_interfaces = bot_interfaces
        self.command_handler = command_handler
        self.persona_engine = persona_engine
        self.session_manager = session_manager
        self.multimodal_processor = multimodal_processor

    async def handle_group_message(self, ws, payload: Dict[str, Any]) -> AgentRunResult:
        group_id = int(payload["group_id"])
        user_id = int(payload["user_id"])
        message_id = payload.get("message_id")
        segments = payload.get("message", [])
        message_content = await self.bot_interfaces["encode_message_to_CQ"](segments)

        # Store every group message so the bot can recall group history later.
        memory = self.session_manager.memory
        if memory:
            memory.store(group_id, user_id, message_content, "user")

        decision = self.decide_group(message_content, segments)
        print(f"[Agent] group decision={decision.action.value} reason={decision.reason}")

        if decision.action == AgentAction.IGNORE:
            return AgentRunResult(False, decision.action, decision.reason)

        if decision.action == AgentAction.TOOL:
            handled = await self.command_handler.handle_command(
                ws,
                MessageType.GROUP,
                decision.command_type,
                message_content,
                group_id=group_id,
                user_id=user_id,
                message_id=message_id,
            )
            return AgentRunResult(handled, decision.action, decision.reason)

        message_content = await self._with_reply_context(ws, segments, message_content)
        message_content = await self.multimodal_processor(segments, message_content)

        persona_prompt = self.persona_engine.prepare(user_id, message_content)
        if persona_prompt.blocked_override:
            print(f"[Persona] Blocked System Override from group user {user_id}")

        group = self.session_manager.get_group_session(group_id)
        response = await group.handle_message(
            user_id,
            persona_prompt.message_content,
            persona_prompt.system_role,
            store_user=False,
        )
        segments = await self._build_message_segments(response)
        await self._send_with_human_delay(ws, group_id, segments, is_group=True)
        return AgentRunResult(True, decision.action, decision.reason)

    async def handle_private_message(self, ws, payload: Dict[str, Any]) -> AgentRunResult:
        user_id = int(payload["user_id"])
        segments = payload.get("message", [])
        message_content = await self.bot_interfaces["encode_message_to_CQ"](segments)

        decision = self.decide_private(message_content)
        print(f"[Agent] private decision={decision.action.value} reason={decision.reason}")

        if decision.action == AgentAction.TOOL:
            handled = await self.command_handler.handle_command(
                ws,
                MessageType.PRIVATE,
                decision.command_type,
                message_content,
                user_id=user_id,
            )
            return AgentRunResult(handled, decision.action, decision.reason)

        message_content = await self.multimodal_processor(segments, message_content)

        persona_prompt = self.persona_engine.prepare(user_id, message_content)
        if persona_prompt.blocked_override:
            print(f"[Persona] Blocked System Override from private user {user_id}")
        print(f"[Persona] Using {persona_prompt.mode} mode for private user {user_id}")

        user_session = self.session_manager.get_private_session(user_id)
        response = await user_session.handle_message(
            persona_prompt.message_content,
            persona_prompt.system_role,
        )
        segments = await self._build_message_segments(response)
        await self._send_with_human_delay(ws, user_id, segments, is_group=False)
        return AgentRunResult(True, decision.action, decision.reason)

    async def _build_message_segments(self, response: str) -> List[dict]:
        """Split AI response into a mixed text+image OneBot segment list.

        Handles two special tag types inside the AI response:
          <render_md>…</render_md>  – render the enclosed markdown as a PNG image
          <sticker:name>            – insert the named pre-set sticker image

        Surrounding text may still contain CQ codes and is parsed normally.
        All failures degrade gracefully to plain text so nothing is lost silently.
        """
        if not _SPECIAL_RE.search(response):
            return await self.bot_interfaces["decode_CQ_to_message"](response)

        # split() with one capturing group → [text, tag, text, tag, …]
        parts = _SPECIAL_RE.split(response)
        result: List[dict] = []
        for i, part in enumerate(parts):
            if not part:
                continue
            if i % 2 == 0:
                # plain text segment (may contain CQ codes)
                result.extend(await self.bot_interfaces["decode_CQ_to_message"](part))
            elif part.startswith('<render_md>'):
                md_match = _RENDER_MD_RE.match(part)
                md_content = md_match.group(1).strip() if md_match else part
                md_content = _CQ_RE.sub('', md_content)
                try:
                    img_b64 = await markdown_to_image(md_content)
                    result.append({"type": "image", "data": {"file": f"base64://{img_b64}"}})
                except Exception as exc:
                    print(f"[Agent] markdown render failed, raw md content: {md_content[:200]}")
                    print(f"[Agent] markdown render exception: {exc}")
                    import traceback
                    traceback.print_exc()
                    result.extend(await self.bot_interfaces["decode_CQ_to_message"]("[Markdown 渲染失败，请稍后重试]"))
            elif part.startswith('<sticker:'):
                name = _STICKER_RE.match(part).group(1)
                seg = sticker_to_segment(name)
                if seg:
                    result.append(seg)
                else:
                    print(f"[Agent] sticker not found: {name}")
        return result

    def decide_group(self, message_content: str, segments: list) -> AgentDecision:
        command_type = self.command_handler.get_command_type(message_content)
        if command_type:
            return AgentDecision(AgentAction.TOOL, "matched explicit command", command_type)

        if self._is_at_me(segments):
            return AgentDecision(AgentAction.CHAT, "bot was mentioned")

        return AgentDecision(AgentAction.IGNORE, "group message did not mention bot")

    def decide_private(self, message_content: str) -> AgentDecision:
        command_type = self.command_handler.get_command_type(message_content)
        if command_type:
            return AgentDecision(AgentAction.TOOL, "matched explicit command", command_type)
        return AgentDecision(AgentAction.CHAT, "private message")

    async def _with_reply_context(self, ws, segments: list, message_content: str) -> str:
        reply_id = self._reply_id(segments)
        if not reply_id:
            return message_content

        try:
            reply_message = await self.bot_interfaces["get_message_by_id"](ws, reply_id)
            if not reply_message or "message" not in reply_message:
                print(f"[Agent] Could not fetch replied message with id {reply_id}")
                return message_content

            reply_content = await self.bot_interfaces["encode_message_to_CQ"](
                reply_message["message"]
            )
            return (
                f"The user is replying to this message: '{reply_content}'. "
                f"Their new message is: {message_content}"
            )
        except Exception as exc:
            print(f"[Agent] Error fetching replied message {reply_id}: {exc}")
            return message_content

    def _is_at_me(self, segments: list) -> bool:
        bot_qq = str(self.bot_interfaces["bot_qq"])
        for part in segments:
            if part.get("type") == "at" and str(part.get("data", {}).get("qq")) == bot_qq:
                return True
        return False

    def _reply_id(self, segments: list) -> Optional[str]:
        for part in segments:
            if part.get("type") == "reply":
                reply_id = part.get("data", {}).get("id")
                if reply_id is not None:
                    return str(reply_id)
        return None

    # ------------------------------------------------------------------
    # Human-like sending helpers (anti-fraud / rate-limiting)
    # ------------------------------------------------------------------
    _CHUNK_MAX_LEN = 200  # characters; split if a chunk exceeds this

    async def _send_with_human_delay(
        self, ws, target_id: int, segments: list, is_group: bool
    ) -> None:
        """Send segments with a randomized pre-send delay.

        If the plain-text content exceeds _CHUNK_MAX_LEN characters the
        message is split on sentence boundaries (newlines, periods, etc.)
        and sent in chunks, each with its own inter-chunk delay.
        """
        # 1. Pre-send human-thinking delay (0.8 – 4.0 s)
        await asyncio.sleep(random.uniform(0.8, 4.0))

        # 2. Determine plain-text length for the chunking decision
        full_text = self._segments_to_plain_text(segments)
        if len(full_text) <= self._CHUNK_MAX_LEN:
            # short message → send as-is
            if is_group:
                await self.bot_interfaces["send_group_message"](ws, target_id, segments)
            else:
                await self.bot_interfaces["send_private_message"](ws, target_id, segments)
            return

        # 3. Split into chunks and send one by one
        chunks = self._split_long_text(full_text)
        for chunk in chunks:
            chunk_segments = await self.bot_interfaces["decode_CQ_to_message"](chunk)
            if is_group:
                await self.bot_interfaces["send_group_message"](ws, target_id, chunk_segments)
            else:
                await self.bot_interfaces["send_private_message"](ws, target_id, chunk_segments)
            if chunk is not chunks[-1]:
                # inter-chunk delay (1.0 – 3.0 s)
                await asyncio.sleep(random.uniform(1.0, 3.0))

    @staticmethod
    def _segments_to_plain_text(segments: list) -> str:
        """Extract plain-text content from OneBot segments."""
        text = ""
        for seg in segments:
            if seg.get("type") == "text":
                text += seg.get("data", {}).get("text", "")
        return text

    @staticmethod
    def _split_long_text(text: str) -> List[str]:
        """Split *text* on sentence boundaries so each chunk ≤ _CHUNK_MAX_LEN.

        Boundaries tried in order:
          newline (\\n)  →  punctuation: (. ) / (。) / (！) / (？)
        If a single sentence still exceeds the limit it is cut at the
        limit boundary (hard split).
        """
        # Try splitting on newlines first
        lines = text.split("\n")
        # If at least one line already exceeds the limit, fallback to
        # punctuation-based splitting so we don't cut mid-sentence.
        if any(len(line) > AgentOrchestrator._CHUNK_MAX_LEN for line in lines):
            import re
            # Split on period+space, Chinese period, exclamation, question mark
            parts = re.split(r'(?<=\. )|(?<=。)|(?<=！)|(?<=？)', text)
            # Filter out empty strings
            parts = [p for p in parts if p]
            return AgentOrchestrator._merge_chunks(
                parts, AgentOrchestrator._CHUNK_MAX_LEN
            )
        return AgentOrchestrator._merge_chunks(
            lines, AgentOrchestrator._CHUNK_MAX_LEN
        )

    @staticmethod
    def _merge_chunks(parts: List[str], max_len: int) -> List[str]:
        """Merge *parts* greedily so that each result item ≤ max_len."""
        result = []
        buf = ""
        for part in parts:
            if not part.strip():
                # empty lines – append them to the current buffer so they
                # don't get lost
                if buf:
                    buf += "\n"
                continue
            if not buf:
                buf = part
                continue
            if len(buf) + 1 + len(part) <= max_len:
                buf += "\n" + part
            else:
                result.append(buf)
                buf = part
        if buf:
            result.append(buf)
        return result
