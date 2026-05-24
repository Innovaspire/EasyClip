"""Clip Annotation - annotate video clips with text-to-video prompts using LLM/VLM."""

from easyclip.annotation.project import AnnotationProject, AnnotatedClip, FrameAnnotation
from easyclip.annotation.settings import AnnotationSettings

__all__ = [
    "AnnotationProject",
    "AnnotatedClip",
    "FrameAnnotation",
    "AnnotationSettings",
]
