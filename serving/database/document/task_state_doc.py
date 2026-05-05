# -*- coding: utf-8 -*-
"""
故障任务状态 Document 模型（MongoEngine）。

作用：
  把当前对话的故障排查任务状态持久化到 MongoDB。
  替代原 Redis 存储方案（详见 service/chat/state/task_state.py 设计文档）。

风格对齐：
  - conversation_summary.py（Lane 2 会话摘要）
  - user_profile.py（Lane 3 用户画像）

字段说明：
  task_data 是一个 DictField，存放完整的 TaskState 序列化结果（model_dump(mode='json')）。
  这样 TaskState Pydantic 模型可以独立演进，不用每次加字段都改 Document schema。
"""

from datetime import datetime

from mongoengine import (
    Document,
    StringField,
    DictField,
    DateTimeField,
)


class FaultTaskState(Document):
    """故障任务状态 Document。一个 chat_id 对应一条记录。"""

    meta = {
        'db_alias': 'document',
        'collection': 'fault_task_states',
        'indexes': [
            {'fields': ['chat_id'], 'unique': True},
            'updated_at',
        ]
    }

    chat_id = StringField(required=True, unique=True, max_length=64)

    # task_data 存放 TaskState.model_dump(mode='json') 的完整结果
    # （含 fault_description / cached_retrieval_summary / excluded_causes 等所有字段）
    task_data = DictField(default={})

    # 状态字段冗余出来便于查询过滤（也存在 task_data 内）
    status = StringField(default='ongoing')   # ongoing / resolved / escalated / abandoned

    created_at = DateTimeField(default=datetime.now)
    updated_at = DateTimeField(default=datetime.now)
