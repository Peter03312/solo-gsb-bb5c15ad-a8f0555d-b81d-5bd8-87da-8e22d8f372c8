"""HTTP 入参/出参模型。

关键校验：
- 所有时间字段必须带时区偏移（naive datetime 直接 422）；
- 必填字段缺失同样 422，错误体在 main 中统一为 {"error": {...}}。
"""
import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from . import constants as C


def _aware(dt: datetime, name: str) -> datetime:
    if dt.tzinfo is None:
        raise ValueError(f"{name} 必须带时区偏移，例如 2025-02-11T10:00:00+08:00")
    return dt


_CUTOFF_RE = re.compile(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$")


def validate_cutoff(text: str) -> str:
    """校验当地截止钟点 HH:MM 或 HH:MM:SS（00:00:00～23:59:59），返回归一化 HH:MM:SS。"""
    m = _CUTOFF_RE.match(text or "")
    if not m:
        raise ValueError("截止钟点格式无效，应为 HH:MM 或 HH:MM:SS，例如 18:00 或 17:30:00")
    hh, mm, ss = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
    if not (0 <= hh <= 23 and 0 <= mm <= 59 and 0 <= ss <= 59):
        raise ValueError("截止钟点超出合法范围（小时 0-23、分钟 0-59、秒 0-59）")
    return f"{hh:02d}:{mm:02d}:{ss:02d}"


class CreateOrderRequest(BaseModel):
    order_id: str = Field(min_length=1)
    created_at: datetime
    timezone: str = Field(description="IANA 时区名，如 Asia/Shanghai")
    calendar_code: str = Field(default="CN", description="节假日表代码，默认 CN")
    promise_by: datetime | None = Field(default=None, description="承诺送达时限，可空")
    weight_kg: float | None = None

    @field_validator("created_at", "promise_by")
    @classmethod
    def _tz(cls, v):
        if v is not None:
            _aware(v, "时间字段")
        return v


class EventIn(BaseModel):
    event_id: str = Field(min_length=1)
    event_type: str
    event_time: datetime = Field(description="扫描发生时间，必须带时区偏移")
    arrived_at: datetime = Field(description="事件到达/接收时间，必须带时区偏移；流内必须单调不减")
    note: str = ""

    # 更正事件专用
    correction_action: Literal["amend", "cancel", "insert"] | None = None
    target_event_id: str | None = Field(default=None, description="amend/cancel 时必填：被更正的原事件 ID")

    @field_validator("event_time", "arrived_at")
    @classmethod
    def _tz(cls, v):
        _aware(v, "时间字段")
        return v


class EventBatchRequest(BaseModel):
    events: list[EventIn] = Field(min_length=1)


class EventPreviewRequest(BaseModel):
    """无写入预演入参：一批按到达顺序排列、尚未写入的事件 + 可选判定基准时刻。"""
    events: list[EventIn] = Field(min_length=1)
    as_of: datetime | None = Field(
        default=None, description="判定基准时刻（带时区偏移），缺省取服务当前 UTC 时间")

    @field_validator("as_of")
    @classmethod
    def _tz(cls, v):
        if v is not None:
            _aware(v, "as_of")
        return v


class ClaimSubmitRequest(BaseModel):
    """理赔申请入参：理赔类型 + 幂等键（必填）+ 可选的服务接收时刻（回溯测试用）。"""
    claim_type: str = Field(description="理赔类型：delay | damage | loss")
    idempotency_key: str = Field(
        min_length=1,
        description="客户端幂等键；同订单同键同类型重试返回原凭证，换类型报冲突")
    received_at: datetime | None = Field(
        default=None,
        description="服务实际接收申请的时刻（带时区偏移），缺省取服务当前 UTC 时间")

    @field_validator("received_at")
    @classmethod
    def _tz(cls, v):
        if v is not None:
            _aware(v, "received_at")
        return v


class CalendarDiagnoseRequest(BaseModel):
    """节假日表诊断入参：日历代码 + 时区 + 带偏移起始时刻 + 1～60 个工作日 + 当地截止钟点。"""
    calendar_code: str = Field(min_length=1, description="现有节假日表代码，如 CN")
    timezone: str = Field(description="IANA 时区标识，如 Asia/Shanghai")
    start_at: datetime = Field(
        description="带时区偏移的起始时刻（ISO8601），诊断自其当地日期的次日起逐日展开")
    business_days: int = Field(
        ge=1, le=60, description="需要推算的工作日个数，取值 1～60")
    cutoff: str = Field(
        description="第 N 个工作日当天的当地截止钟点，HH:MM 或 HH:MM:SS，例如 18:00")

    @field_validator("start_at")
    @classmethod
    def _tz(cls, v):
        return _aware(v, "start_at")

    @field_validator("cutoff")
    @classmethod
    def _cutoff(cls, v):
        return validate_cutoff(v)
