from .base import ArticleAdapter, Detection
from .wechat import WeChatAdapter
from .zhihu import ZhihuColumnAdapter

ADAPTERS: tuple[ArticleAdapter, ...] = (WeChatAdapter(), ZhihuColumnAdapter())

__all__ = [
    "ADAPTERS",
    "ArticleAdapter",
    "Detection",
    "WeChatAdapter",
    "ZhihuColumnAdapter",
]
