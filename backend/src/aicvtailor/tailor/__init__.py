"""Tailoring: apply accepted suggestions, compile, diff, verify."""

from .compile import CompileResult, GatedCompile, compile_tex, compile_with_gate, detect_engine
from .diff import ChangedSpan, Piece, word_diff
from .pipeline import TailorResult, tailor
from .verify import Verification, extract_text, verify

__all__ = [
    "ChangedSpan",
    "CompileResult",
    "GatedCompile",
    "Piece",
    "TailorResult",
    "Verification",
    "compile_tex",
    "compile_with_gate",
    "detect_engine",
    "extract_text",
    "tailor",
    "verify",
    "word_diff",
]
