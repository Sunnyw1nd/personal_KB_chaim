"""Offline normalizer for recognized WeChat and Zhihu saved-HTML articles."""

from .batch import (
    BatchNormalizationError,
    BatchNormalizationSummary,
    normalize_sample_archives,
)
from .migration import MigrationPreviewError, preview_legacy_outputs
from .normalizer import (
    NormalizationError,
    normalize_saved_article,
    normalize_wechat_article,
    normalize_zhihu_article,
)
from .sample_cache import PreparedSample, SamplePreparationError, prepare_sample
from .version import __version__

__all__ = [
    "BatchNormalizationError",
    "BatchNormalizationSummary",
    "MigrationPreviewError",
    "NormalizationError",
    "PreparedSample",
    "SamplePreparationError",
    "__version__",
    "normalize_sample_archives",
    "normalize_saved_article",
    "normalize_wechat_article",
    "normalize_zhihu_article",
    "prepare_sample",
    "preview_legacy_outputs",
]
