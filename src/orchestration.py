"""集体婚典编排领域服务。

设计要点：

- 授权先行：身份核验、公开展示、婚书制作、市集服务分别取得新人明确授权，
  撤回只影响尚未执行的环节。
- 计划与记录分离：大屏顺序、巡游位置、婚书版本等“计划”随最新状态投影；
  环节一旦完成，当时使用的姓名、素材版本、婚书版本即固化为不可变记录。
- 最小知情：现场岗位只能读取当前环节所需字段，任何岗位视图都不返回
  法定姓名、证件凭据、礼服尺寸等无关资料。
- 审计留痕：授权、修订、跳序、取消、设备故障、到期删除均写入只增审计日志。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum


class Scope(str, Enum):
    """新人授权范围。"""

    IDENTITY = "identity_verification"  # 身份核验，活动后按期删除证件资料
    PUBLIC_DISPLAY = "public_display"  # 大屏、影像、当众诵读等公开呈现
    CERTIFICATE = "certificate"  # 婚书制作与签署（婚书长期保留）
    MARKET = "market_service"  # 市集服务预约


class SegmentKind(str, Enum):
    PARADE = "parade"
    MEDIA_ROLL = "media_roll"
    ENTRANCE = "entrance"
    CEREMONY = "ceremony"
    CERTIFICATE = "certificate"
    MARKET = "market"


class Station(str, Enum):
    PARADE_MARSHAL = "parade_marshal"  # 巡游引导
    SCREEN_OPERATOR = "screen_operator"  # 大屏影像
    USHER = "usher"  # 入场礼仪
    RITUAL_OFFICER = "ritual_officer"  # 古礼执礼
    CERT_STEWARD = "cert_steward"  # 婚书执事
    MARKET_DESK = "market_desk"  # 市集服务台


# 岗位可在岗的环节；大屏岗负责影像播放与婚书电子展示两个有屏环节。
STATION_SEGMENTS = {
    Station.PARADE_MARSHAL: (SegmentKind.PARADE,),
    Station.SCREEN_OPERATOR: (SegmentKind.MEDIA_ROLL, SegmentKind.CERTIFICATE),
    Station.USHER: (SegmentKind.ENTRANCE,),
    Station.RITUAL_OFFICER: (SegmentKind.CEREMONY,),
    Station.CERT_STEWARD: (SegmentKind.CERTIFICATE,),
    Station.MARKET_DESK: (SegmentKind.MARKET,),
}

# 各环节固定先后，迟到跳序等变更会沿此顺序向后续环节传播。
DEFAULT_SEGMENTS = (
    ("seg-parade", SegmentKind.PARADE, "巡游", None),
    ("seg-media", SegmentKind.MEDIA_ROLL, "影像播放", "screen"),
    ("seg-entrance", SegmentKind.ENTRANCE, "入场礼序", None),
    ("seg-ceremony", SegmentKind.CEREMONY, "古礼与集体诵读", None),
    ("seg-cert", SegmentKind.CERTIFICATE, "婚书签署与展示", "screen"),
    ("seg-market", SegmentKind.MARKET, "市集服务", None),
)

# 授权对应资料活动后的默认保留天数；None 表示长期保留（婚书）。
DEFAULT_RETENTION_DAYS = {
    Scope.IDENTITY: 30,
    Scope.PUBLIC_DISPLAY: 30,
    Scope.CERTIFICATE: None,
    Scope.MARKET: 90,
}

SENSITIVE_FIELDS = ("legal_name", "id_doc_token", "attire_size", "contact")


class OrchestrationError(ValueError):
    """编排规则被违反。"""


@dataclass
class Consent:
    scope: Scope
    granted: bool = False
    granted_at: datetime | None = None
    revoked_at: datetime | None = None
    delete_due: date | None = None
    purged_at: datetime | None = None

    @property
    def active(self) -> bool:
        return self.granted and self.revoked_at is None and self.purged_at is None


@dataclass
class Person:
    person_id: str
    org_category: str
    display_name: str | None = None
    legal_name: str | None = None  # 敏感：仅用于核验，任何岗位视图不输出
    id_doc_token: str | None = None  # 敏感：证件保管库的不透明凭据
    contact: str | None = None  # 敏感
    attire_size: str | None = None
    verified: bool = False
    consents: dict[Scope, Consent] = field(default_factory=dict)


@dataclass
class MediaAsset:
    asset_id: str
    version: int
    state: str  # current / replaced / withdrawn / purged
    created_at: datetime
    note: str = ""


@dataclass
class CertVersion:
    version_no: int
    names: dict[str, str]  # person_id -> 签署时展示姓名快照
    electronic: str  # draft / published / superseded / signed
    physical: str  # not_printed / printed / void
    signed: bool = False
    delivered: bool = False
    created_at: datetime | None = None
    reason: str = ""


@dataclass
class Booking:
    code: str
    service: str
    slot: str
    state: str = "booked"  # booked / cancelled / delivered
    delivered_at: datetime | None = None


@dataclass
class Couple:
    couple_id: str
    couple_no: int
    person_a: str
    person_b: str
    parade_position: int | None = None
    entrance_position: int | None = None
    role: str = "新人"
    status: str = "participating"  # participating / cancelled
    assets: list[MediaAsset] = field(default_factory=list)
    cert_versions: list[CertVersion] = field(default_factory=list)
    cert_current_no: int | None = None
    bookings: list[Booking] = field(default_factory=list)
    cancelled_at: datetime | None = None


@dataclass
class Segment:
    segment_id: str
    kind: SegmentKind
    title: str
    order: int
    device_kind: str | None
    status: str = "scheduled"  # scheduled / active / completed


@dataclass
class ItemRecord:
    """环节执行后不可变的事实记录。"""

    item_id: str
    segment_id: str
    couple_id: str
    outcome: str  # done / skipped / failed
    at: datetime
    actor: str
    snapshot: dict
    reason: str = ""
    device_id: str | None = None


@dataclass
class GapMarker:
    couple_id: str
    position: int
    note: str


@dataclass
class Device:
    device_id: str
    kind: str
    ok: bool = True


@dataclass
class AuditEntry:
    seq: int
    at: datetime
    actor: str
    action: str
    target: str
    detail: dict = field(default_factory=dict)
    reason: str = ""


class WeddingEvent:
    """一场集体婚典的聚合根。"""

    def __init__(self, event_date: date, *, now: datetime | None = None):
        self.event_date = event_date
        self.segments: list[Segment] = []
        for order, (sid, kind, title, device_kind) in enumerate(DEFAULT_SEGMENTS, start=1):
            self.segments.append(Segment(sid, kind, title, order, device_kind))
        self.persons: dict[str, Person] = {}
        self.couples: dict[str, Couple] = {}
        self._couple_no = 0
        self.devices: dict[str, Device] = {}
        self.market_capacity: dict[tuple[str, str], int] = {}
        self.records: list[ItemRecord] = []
        self.gap_markers: dict[str, list[GapMarker]] = {}
        self.deferred_media: list[dict] = []
        self.audit: list[AuditEntry] = []
        self._started: dict[str, datetime] = {}
        self._closed: dict[str, datetime] = {}
        self._now = now

    # ---------- 基础工具 ----------

    def _clock(self, now: datetime | None) -> datetime:
        value = now or self._now or datetime.now()
        self._now = value
        return value

    def _person(self, person_id: str) -> Person:
        try:
            return self.persons[person_id]
        except KeyError:
            raise OrchestrationError(f"未登记的新人：{person_id}") from None

    def _couple_of(self, person_id: str) -> Couple:
        for couple in self.couples.values():
            if person_id in (couple.person_a, couple.person_b):
                return couple
        raise OrchestrationError(f"新人尚未配对：{person_id}")

    def _segment(self, kind: SegmentKind) -> Segment:
        return next(s for s in self.segments if s.kind == kind)

    def _active_segment(self) -> Segment | None:
        for segment in self.segments:
            if segment.status == "active":
                return segment
        return None

    def _log(self, at: datetime, actor: str, action: str, target: str,
             detail: dict | None = None, reason: str = "") -> None:
        self.audit.append(AuditEntry(
            seq=len(self.audit) + 1, at=at, actor=actor, action=action,
            target=target, detail=detail or {}, reason=reason,
        ))

    def _consent(self, person: Person, scope: Scope) -> Consent:
        return person.consents.get(scope) or Consent(scope=scope)

    def _couple_consent_active(self, couple: Couple, scope: Scope) -> bool:
        return all(
            self._consent(self.persons[pid], scope).active
            for pid in (couple.person_a, couple.person_b)
        )

    def _names(self, couple: Couple) -> dict[str, str]:
        return {
            pid: (self.persons[pid].display_name or self.persons[pid].person_id)
            for pid in (couple.person_a, couple.person_b)
        }

    def _participating(self) -> list[Couple]:
        return [c for c in self.couples.values() if c.status == "participating"]

    def _current_asset(self, couple: Couple) -> MediaAsset | None:
        for asset in reversed(couple.assets):
            if asset.state == "current":
                return asset
        return None

    def _current_cert(self, couple: Couple) -> CertVersion | None:
        if couple.cert_current_no is None:
            return None
        return couple.cert_versions[couple.cert_current_no - 1]

    def _public_allowed(self, couple: Couple) -> bool:
        return self._couple_consent_active(couple, Scope.PUBLIC_DISPLAY)

    # ---------- 登记、授权与核验 ----------

    def register_person(self, person_id: str, org_category: str) -> Person:
        if person_id in self.persons:
            raise OrchestrationError(f"新人已登记：{person_id}")
        if org_category not in ("高校", "科研院所", "企业"):
            raise OrchestrationError("单位类别须为高校、科研院所或企业")
        person = Person(person_id=person_id, org_category=org_category)
        self.persons[person_id] = person
        return person

    def grant_consent(self, person_id: str, scope: Scope, *,
                      actor: str = "新人", now: datetime | None = None,
                      delete_due: date | None = None) -> Consent:
        at = self._clock(now)
        person = self._person(person_id)
        consent = person.consents.get(scope)
        if consent is None:
            consent = Consent(scope=scope)
            person.consents[scope] = consent
        if not consent.granted:
            consent.granted = True
            consent.granted_at = at
            consent.purged_at = None
            days = DEFAULT_RETENTION_DAYS[scope]
            consent.delete_due = delete_due or (
                self.event_date + timedelta(days=days) if days is not None else None
            )
        # 撤回后重新授权：重新生效；删除期限若已过则不复活，需重新走授权流程。
        consent.revoked_at = None
        if consent.purged_at is not None:
            raise OrchestrationError("资料已按期删除，须重新登记授权")
        self._log(at, actor, "grant_consent", person_id,
                  {"scope": scope.value, "delete_due": str(consent.delete_due) if consent.delete_due else None})
        return consent

    def revoke_consent(self, person_id: str, scope: Scope, *,
                       reason: str = "", actor: str = "新人",
                       now: datetime | None = None) -> None:
        """撤回授权：立即从所有未执行环节的计划中移除相应呈现。"""
        at = self._clock(now)
        person = self._person(person_id)
        consent = self._consent(person, scope)
        if not consent.granted:
            raise OrchestrationError("尚未授权，无法撤回")
        if consent.revoked_at is not None:
            raise OrchestrationError("授权已撤回")
        consent.revoked_at = at
        couple = self._couple_of(person_id)
        if scope == Scope.PUBLIC_DISPLAY:
            for asset in couple.assets:
                if asset.state == "current":
                    asset.state = "withdrawn"
        if scope == Scope.CERTIFICATE:
            cert = self._current_cert(couple)
            if cert is not None and not cert.signed:
                self._supersede_cert(couple, at, "婚书授权撤回，未签署版本停用")
        if scope == Scope.MARKET:
            for booking in couple.bookings:
                if booking.state == "booked":
                    booking.state = "cancelled"
        self._log(at, actor, "revoke_consent", person_id, {"scope": scope.value}, reason)

    def _supersede_cert(self, couple: Couple, at: datetime, reason: str) -> CertVersion:
        """作废旧版（已印实体标记 void、电子标记 superseded），开出待重批草稿。"""
        cert = self._current_cert(couple)
        if cert is None or cert.signed:
            raise OrchestrationError("已签署婚书不可作废")
        if cert.physical == "printed":
            cert.physical = "void"
        cert.electronic = "superseded"
        new = CertVersion(
            version_no=cert.version_no + 1,
            names=dict(self._names(couple)),
            electronic="draft", physical="not_printed",
            created_at=at, reason=reason,
        )
        couple.cert_versions.append(new)
        couple.cert_current_no = new.version_no
        return new

    def verify_identity(self, person_id: str, legal_name: str, id_doc_token: str, *,
                        actor: str = "核验岗", now: datetime | None = None) -> None:
        """在明确授权后记录核验结论；只保存不透明凭据，绝不保存证件影像。"""
        at = self._clock(now)
        person = self._person(person_id)
        if not self._consent(person, Scope.IDENTITY).active:
            raise OrchestrationError("身份核验须先取得本人授权")
        person.legal_name = legal_name
        person.id_doc_token = id_doc_token
        person.verified = True
        self._log(at, actor, "verify_identity", person_id, {"verified": True})

    def set_display_name(self, person_id: str, display_name: str, *,
                         actor: str = "新人", now: datetime | None = None) -> None:
        at = self._clock(now)
        person = self._person(person_id)
        if not display_name.strip():
            raise OrchestrationError("展示姓名不能为空")
        old_name = person.display_name
        person.display_name = display_name.strip()
        detail = {"old": old_name, "new": person.display_name}
        try:
            couple = self._couple_of(person_id)
        except OrchestrationError:
            self._log(at, actor, "revise_display_name", person_id, detail)
            return
        cert = self._current_cert(couple)
        if cert is not None:
            if cert.signed:
                # 已签署交付的婚书保留原记录，仅后续大屏等环节使用新姓名。
                detail["certificate"] = "signed_version_retained"
            elif cert.physical == "printed":
                # 已印实体须作废旧版、重制新版，重批重印前电子/实体处于不一致窗口。
                new = self._supersede_cert(couple, at, "姓名修订，旧版实体婚书作废")
                detail["certificate"] = f"draft_v{new.version_no}_pending_approval"
            else:
                cert.names = dict(self._names(couple))
                detail["certificate"] = "snapshot_updated_before_print"
        self._log(at, actor, "revise_display_name", person_id, detail)

    def set_attire(self, person_id: str, size: str) -> None:
        self._person(person_id).attire_size = size

    # ---------- 配对、位置、角色 ----------

    def pair_couple(self, couple_id: str, person_a: str, person_b: str, *,
                    now: datetime | None = None) -> Couple:
        at = self._clock(now)
        if couple_id in self.couples:
            raise OrchestrationError(f"婚侣编号已存在：{couple_id}")
        if person_a == person_b:
            raise OrchestrationError("不能与本人配对")
        for pid in (person_a, person_b):
            person = self._person(pid)
            if not person.verified:
                raise OrchestrationError(f"配对前须完成身份核验：{pid}")
        self._couple_no += 1
        couple = Couple(couple_id=couple_id, couple_no=self._couple_no,
                        person_a=person_a, person_b=person_b)
        self.couples[couple_id] = couple
        self._log(at, "系统", "pair_couple", couple_id,
                  {"couple_no": couple.couple_no, "persons": [person_a, person_b]})
        return couple

    def set_position(self, couple_id: str, position: int, *,
                     actor: str = "编排岗", now: datetime | None = None) -> None:
        """巡游与入场位置同一套礼序，原子更新，保证两环节精确对应。"""
        at = self._clock(now)
        couple = self._couple(couple_id)
        for other in self._participating():
            if other is not couple and (
                other.parade_position == position or other.entrance_position == position
            ):
                raise OrchestrationError(f"礼序位置 { position } 已被占用")
        couple.parade_position = position
        couple.entrance_position = position
        self._log(at, actor, "set_position", couple_id, {"position": position})

    def assign_role(self, couple_id: str, role: str) -> None:
        self._couple(couple_id).role = role

    def _couple(self, couple_id: str) -> Couple:
        try:
            return self.couples[couple_id]
        except KeyError:
            raise OrchestrationError(f"未登记的婚侣：{couple_id}") from None

    # ---------- 影像素材 ----------

    def submit_media(self, couple_id: str, asset_id: str, *, note: str = "",
                     actor: str = "影像岗", now: datetime | None = None) -> None:
        at = self._clock(now)
        couple = self._couple(couple_id)
        if any(a.asset_id == asset_id for a in couple.assets):
            raise OrchestrationError("素材标识重复")
        if couple.assets:
            raise OrchestrationError("已有素材时应使用 replace_media 提交新版本")
        couple.assets.append(MediaAsset(asset_id, 1, "current", at, note))
        self._log(at, actor, "submit_media", couple_id, {"asset_id": asset_id, "version": 1})

    def replace_media(self, couple_id: str, new_asset_id: str, *, note: str = "",
                      actor: str = "影像岗", now: datetime | None = None) -> None:
        """替换素材：未播环节改用新版本；已播环节的记录保留旧版本。"""
        at = self._clock(now)
        couple = self._couple(couple_id)
        current = self._current_asset(couple)
        if current is None:
            raise OrchestrationError("尚无已审核素材，应先 submit_media")
        if any(a.asset_id == new_asset_id for a in couple.assets):
            raise OrchestrationError("素材标识重复")
        current.state = "replaced"
        new = MediaAsset(new_asset_id, current.version + 1, "current", at, note)
        couple.assets.append(new)
        self._log(at, actor, "replace_media", couple_id,
                  {"old_asset": current.asset_id, "old_version": current.version,
                   "new_asset": new_asset_id, "new_version": new.version})

    # ---------- 婚书版本 ----------

    def approve_certificate(self, couple_id: str, *, actor: str = "婚书岗",
                            now: datetime | None = None) -> CertVersion:
        """发布电子婚书；实体婚书只能依据同一姓名快照付印。"""
        at = self._clock(now)
        couple = self._couple(couple_id)
        if not self._couple_consent_active(couple, Scope.CERTIFICATE):
            raise OrchestrationError("婚书制作须双方授权且未撤回")
        names = self._names(couple)
        if any(name is None or name in (couple.person_a, couple.person_b) for name in names.values()):
            raise OrchestrationError("发布婚书前须确认双方展示姓名")
        cert = self._current_cert(couple)
        if cert is None:
            cert = CertVersion(version_no=1, names=dict(names), electronic="published",
                               physical="not_printed", created_at=at, reason="首次发布")
            couple.cert_versions.append(cert)
            couple.cert_current_no = 1
        elif cert.electronic == "draft":
            cert.names = dict(names)
            cert.electronic = "published"
            cert.reason += "；修订后重新发布"
        elif cert.electronic == "published":
            raise OrchestrationError("当前电子婚书已发布")
        else:
            raise OrchestrationError("当前婚书版本不可发布")
        self._log(at, actor, "approve_certificate", couple_id, {"version": cert.version_no})
        return cert

    def print_certificate(self, couple_id: str, *, actor: str = "婚书岗",
                          now: datetime | None = None) -> None:
        at = self._clock(now)
        couple = self._couple(couple_id)
        cert = self._current_cert(couple)
        if cert is None or cert.electronic != "published":
            raise OrchestrationError("电子婚书未发布，实体婚书不得付印")
        cert.physical = "printed"
        self._log(at, actor, "print_certificate", couple_id, {"version": cert.version_no})

    def certificate_consistency(self, couple: Couple) -> tuple[bool, str]:
        """返回电子与实体婚书是否一致及原因。"""
        cert = self._current_cert(couple)
        if cert is None:
            return False, "未发布婚书"
        if cert.signed:
            return True, f"v{cert.version_no} 已签署交付，记录锁定"
        if cert.electronic == "draft":
            return False, f"v{cert.version_no} 电子婚书待发布（实体旧版已作废）"
        live_names = self._names(couple)
        if cert.names != live_names:
            return False, "展示姓名已变更，婚书版本待重批"
        if cert.physical == "void":
            return False, f"v{cert.version_no} 实体婚书已作废，需重印"
        if cert.physical == "not_printed":
            return False, f"v{cert.version_no} 电子已发布，实体尚未付印，不可签署"
        return True, f"v{cert.version_no} 电子与实体一致"

    # ---------- 市集预约 ----------

    def add_market_service(self, service: str, slot: str, capacity: int) -> None:
        self.market_capacity[(service, slot)] = capacity

    def book_market(self, couple_id: str, service: str, slot: str, *,
                    actor: str = "市集岗", now: datetime | None = None) -> Booking:
        at = self._clock(now)
        couple = self._couple(couple_id)
        if not self._couple_consent_active(couple, Scope.MARKET):
            raise OrchestrationError("市集预约须双方授权")
        capacity = self.market_capacity.get((service, slot), 0)
        used = sum(
            1 for c in self.couples.values()
            for b in c.bookings
            if b.service == service and b.slot == slot and b.state == "booked"
        )
        if used >= capacity:
            raise OrchestrationError("该市集时段已满")
        code = f"MK-{couple.couple_no:02d}-{len(couple.bookings) + 1:02d}"
        booking = Booking(code=code, service=service, slot=slot)
        couple.bookings.append(booking)
        self._log(at, actor, "book_market", couple_id,
                  {"code": code, "service": service, "slot": slot})
        return booking

    # ---------- 设备 ----------

    def register_device(self, device_id: str, kind: str = "screen") -> None:
        self.devices[device_id] = Device(device_id, kind)

    def mark_device_fault(self, device_id: str, *, reason: str = "",
                          actor: str = "设备岗", now: datetime | None = None) -> None:
        """设备故障：未执行播放项立即改道备用设备或转入后续有屏环节。"""
        at = self._clock(now)
        device = self.devices.get(device_id)
        if device is None:
            raise OrchestrationError("未登记的设备")
        device.ok = False
        self._log(at, actor, "device_fault", device_id, {}, reason)

    def mark_device_recovered(self, device_id: str, *, now: datetime | None = None) -> None:
        at = self._clock(now)
        self.devices[device_id].ok = True
        self._log(at, "设备岗", "device_recovered", device_id)

    def _assign_device(self, kind: str) -> Device | None:
        for device in self.devices.values():
            if device.kind == kind and device.ok:
                return device
        return None

    # ---------- 环节执行 ----------

    def start_segment(self, kind: SegmentKind, *, actor: str = "现场调度",
                      now: datetime | None = None) -> None:
        at = self._clock(now)
        current = self._active_segment()
        if current is not None:
            raise OrchestrationError(f"环节 {current.title} 尚未结束")
        segment = self._segment(kind)
        if segment.status != "scheduled":
            raise OrchestrationError("环节已执行过")
        segment.status = "active"
        self._started[segment.segment_id] = at
        self._log(at, actor, "start_segment", segment.segment_id, {"kind": kind.value})

    def close_segment(self, kind: SegmentKind, *, actor: str = "现场调度",
                      now: datetime | None = None) -> None:
        at = self._clock(now)
        segment = self._segment(kind)
        if segment.status != "active":
            raise OrchestrationError("环节不在进行中")
        segment.status = "completed"
        self._closed[segment.segment_id] = at
        self._log(at, actor, "close_segment", segment.segment_id)

    def _ordered(self, couples: list[Couple], attr: str) -> list[Couple]:
        return sorted(
            couples,
            key=lambda c: (getattr(c, attr) is None, getattr(c, attr) or 0, c.couple_no),
        )

    def plan_segment(self, kind: SegmentKind) -> list[dict]:
        """某环节按当前状态投影出的最新计划（不含已完成项）。"""
        couples = self._ordered(self._participating(), "parade_position")
        if kind is SegmentKind.PARADE:
            return [{
                "item_id": c.couple_id, "couple_id": c.couple_id, "couple_no": c.couple_no,
                "action": "walk", "position": c.parade_position,
                "names": list(self._names(c).values()),
                "do_not_display": not self._public_allowed(c),
            } for c in couples if c.parade_position is not None]
        if kind is SegmentKind.ENTRANCE:
            couples = self._ordered(self._participating(), "entrance_position")
            return [{
                "item_id": c.couple_id, "couple_id": c.couple_id, "couple_no": c.couple_no,
                "action": "enter", "position": c.entrance_position,
                "names": list(self._names(c).values()),
                "do_not_display": not self._public_allowed(c),
            } for c in couples if c.entrance_position is not None]
        if kind is SegmentKind.MEDIA_ROLL:
            return self._media_plan(couples)
        if kind is SegmentKind.CEREMONY:
            return [{
                "item_id": c.couple_id, "couple_id": c.couple_id, "couple_no": c.couple_no,
                "action": "ritual", "role": c.role,
                "names": list(self._names(c).values()),
                "read_aloud": self._public_allowed(c),
            } for c in couples]
        if kind is SegmentKind.CERTIFICATE:
            return [self._cert_plan(c) for c in couples]
        if kind is SegmentKind.MARKET:
            items = []
            for c in couples:
                for booking in c.bookings:
                    if booking.state == "booked":
                        items.append({
                            "item_id": booking.code, "couple_id": c.couple_id,
                            "couple_no": c.couple_no, "action": "deliver",
                            "code": booking.code, "service": booking.service, "slot": booking.slot,
                        })
            return items
        raise OrchestrationError("未知环节")

    def _media_plan(self, couples: list[Couple], *, deferred_only: bool = False,
                    include_deferred: bool = False) -> list[dict]:
        items: list[dict] = []
        screen = self._assign_device("screen")
        if include_deferred or deferred_only:
            for deferred in self.deferred_media:
                items.append({
                    "item_id": f"{deferred['couple_id']}:deferred-media",
                    "couple_id": deferred["couple_id"],
                    "couple_no": self.couples[deferred["couple_id"]].couple_no,
                    "action": "play_deferred", "position": None,
                    "names": list(self._names(self.couples[deferred["couple_id"]]).values()),
                    "asset_id": deferred["asset_id"], "version": deferred["version"],
                    "device_id": screen.device_id if screen else None,
                    "note": f"由 {deferred['from_segment']} 顺延",
                })
        if deferred_only:
            return items
        for c in couples:
            base = {"item_id": c.couple_id, "couple_id": c.couple_id, "couple_no": c.couple_no,
                    "position": c.parade_position, "names": list(self._names(c).values())}
            if not self._public_allowed(c):
                items.append({**base, "action": "suppress", "reason": "新人已撤回公开授权"})
                continue
            asset = self._current_asset(c)
            if asset is None:
                items.append({**base, "action": "hold", "reason": "缺少已审核素材"})
            elif screen is None:
                items.append({**base, "action": "defer", "reason": "无可用播放设备",
                              "asset_id": asset.asset_id, "version": asset.version})
            else:
                items.append({**base, "action": "play", "asset_id": asset.asset_id,
                              "version": asset.version, "device_id": screen.device_id})
        return items

    def _cert_plan(self, couple: Couple) -> dict:
        cert = self._current_cert(couple)
        consistent, note = self.certificate_consistency(couple)
        names = list(self._names(couple).values())
        item = {
            "item_id": couple.couple_id, "couple_id": couple.couple_id,
            "couple_no": couple.couple_no, "action": "sign",
            "position": couple.entrance_position, "names": names,
            "cert_version": cert.version_no if cert else None,
            "electronic": cert.electronic if cert else "none",
            "physical": cert.physical if cert else "none",
            "signed": bool(cert and cert.signed),
            "consistent": consistent, "note": note,
            "show_electronic": self._public_allowed(couple),
        }
        if not self._couple_consent_active(couple, Scope.CERTIFICATE):
            item["action"] = "withhold"
            item["note"] = "婚书授权已撤回，不得出示"
        elif not consistent:
            item["action"] = "block"
        return item

    def _records_for(self, segment_id: str) -> dict[str, ItemRecord]:
        return {r.item_id: r for r in self.records if r.segment_id == segment_id}

    def complete_item(self, kind: SegmentKind, couple_id_or_code: str, *,
                      outcome: str = "done", reason: str = "", actor: str = "岗位",
                      device_id: str | None = None, now: datetime | None = None) -> ItemRecord:
        """完成一个环节项，并把当时的姓名/素材/婚书版本固化为不可变快照。"""
        at = self._clock(now)
        segment = self._segment(kind)
        if segment.status != "active":
            raise OrchestrationError("只能在环节进行中记录执行结果")
        if outcome not in ("done", "skipped", "failed"):
            raise OrchestrationError("执行结果只能是 done / skipped / failed")
        plan = self.plan_segment(kind)
        item = next((p for p in plan if p["item_id"] == couple_id_or_code), None)
        deferred_play = None
        if item is None and kind is SegmentKind.CERTIFICATE:
            # 允许在婚书环节补播此前因设备故障顺延的影像。
            deferred_play = next(
                (p for p in self._media_plan([], deferred_only=True)
                 if p["item_id"] == couple_id_or_code), None,
            )
            if deferred_play is not None:
                item = deferred_play
        if item is None:
            raise OrchestrationError("该项目不在当前环节计划中（可能已完成或被变更移除）")
        if kind is SegmentKind.MEDIA_ROLL and item["action"] in ("defer", "hold", "suppress"):
            raise OrchestrationError(f"项目动作为 {item['action']}，应使用 defer_item/skip_suppressed 处理")
        if (kind is SegmentKind.CERTIFICATE and deferred_play is None
                and item["action"] in ("block", "withhold")):
            raise OrchestrationError("婚书状态不允许签署：" + item["note"])

        couple = self._couple(item["couple_id"])
        snapshot = {
            "couple_no": couple.couple_no,
            "names": dict(self._names(couple)),
            "position": item.get("position"),
        }
        is_deferred_play = deferred_play is not None
        if kind is SegmentKind.MEDIA_ROLL or is_deferred_play:
            snapshot["media"] = {"asset_id": item.get("asset_id"), "version": item.get("version"),
                                 "deferred": is_deferred_play}
            device_id = device_id or item.get("device_id")
        if kind is SegmentKind.CERTIFICATE and not is_deferred_play:
            cert = self._current_cert(couple)
            snapshot["certificate"] = {"version": cert.version_no,
                                       "names": dict(cert.names),
                                       "electronic": cert.electronic,
                                       "physical": cert.physical}
            if outcome == "done":
                if item["action"] != "sign" or not item["consistent"]:
                    raise OrchestrationError("电子与实体婚书核对一致前不得签署")
                cert.signed = True
                cert.delivered = True
                cert.electronic = "signed"
        if kind is SegmentKind.MARKET:
            booking = next(b for b in couple.bookings if b.code == couple_id_or_code)
            if outcome == "done":
                booking.state = "delivered"
                booking.delivered_at = at
            snapshot["booking"] = {"code": booking.code, "service": booking.service, "slot": booking.slot}
        if kind is SegmentKind.CEREMONY:
            snapshot["role"] = couple.role
            snapshot["read_aloud"] = item["read_aloud"]

        record = ItemRecord(
            item_id=item["item_id"], segment_id=segment.segment_id,
            couple_id=couple.couple_id, outcome=outcome, at=at, actor=actor,
            snapshot=snapshot, reason=reason or item.get("note", ""), device_id=device_id,
        )
        self.records.append(record)
        if item["action"] == "play_deferred":
            self.deferred_media = [d for d in self.deferred_media
                                   if d["couple_id"] != couple.couple_id]
        self._log(at, actor, "complete_item", couple.couple_id,
                  {"segment": segment.segment_id, "outcome": outcome,
                   "item_id": record.item_id, **{k: v for k, v in snapshot.items() if k != "names"}},
                  reason)
        return record

    def skip_suppressed(self, kind: SegmentKind, couple_id: str, *,
                        actor: str = "岗位", now: datetime | None = None) -> ItemRecord:
        at = self._clock(now)
        item = next((p for p in self.plan_segment(kind) if p["item_id"] == couple_id), None)
        if item is None or item["action"] != "suppress":
            raise OrchestrationError("该项目并非撤下状态")
        segment = self._segment(kind)
        couple = self._couple(couple_id)
        record = ItemRecord(
            item_id=couple_id, segment_id=segment.segment_id, couple_id=couple_id,
            outcome="skipped", at=at, actor=actor, reason=item["reason"],
            snapshot={"couple_no": couple.couple_no, "names": dict(self._names(couple)),
                      "public_display": False},
        )
        self.records.append(record)
        self._log(at, actor, "skip_suppressed", couple_id, {"segment": segment.segment_id}, item["reason"])
        return record

    def defer_item(self, kind: SegmentKind, couple_id: str, *, reason: str = "",
                   actor: str = "岗位", now: datetime | None = None) -> None:
        """无可用设备等原因把未播项顺延到下一个有屏环节。"""
        at = self._clock(now)
        item = next((p for p in self.plan_segment(kind) if p["item_id"] == couple_id), None)
        if item is None or item["action"] != "defer":
            raise OrchestrationError("该项目当前不可顺延")
        self.deferred_media.append({
            "couple_id": couple_id, "asset_id": item["asset_id"],
            "version": item["version"], "from_segment": self._segment(kind).segment_id,
        })
        # 以 failed 记录固化“本环节未播”事实，但素材会在后续环节重播。
        segment = self._segment(kind)
        couple = self._couple(couple_id)
        self.records.append(ItemRecord(
            item_id=couple_id, segment_id=segment.segment_id, couple_id=couple_id,
            outcome="failed", at=at, actor=actor, reason=reason or item["reason"],
            snapshot={"couple_no": couple.couple_no, "names": dict(self._names(couple)),
                      "media": {"asset_id": item["asset_id"], "version": item["version"]},
                      "deferred": True},
        ))
        self._log(at, actor, "defer_media", couple_id,
                  {"from_segment": segment.segment_id, "asset_id": item["asset_id"]}, reason)

    def report_late(self, couple_id: str, *, reason: str = "新人迟到",
                    actor: str = "现场调度", now: datetime | None = None) -> dict:
        """迟到跳序：当前巡游/入场项跳过原位，整对新人顺延至队尾。

        新位置同时写入巡游与入场礼序，后续环节自动按新顺序执行。
        """
        at = self._clock(now)
        couple = self._couple(couple_id)
        active = self._active_segment()
        if active is None or active.kind not in (SegmentKind.PARADE, SegmentKind.ENTRANCE):
            raise OrchestrationError("迟到跳序只能在巡游或入场进行中处理")
        done = self._records_for(active.segment_id)
        if couple_id in done:
            raise OrchestrationError("该对新人已完成当前环节，不能跳序")
        attr = "parade_position" if active.kind is SegmentKind.PARADE else "entrance_position"
        old_position = getattr(couple, attr)
        if old_position is None:
            raise OrchestrationError("该对新人尚未排入礼序")
        others = [c for c in self._participating() if c is not couple]
        new_position = max(
            [getattr(c, attr) or 0 for c in others] + [old_position]
        ) + 1
        setattr(couple, attr, new_position)
        # 两个顺序锁步更新，保证精确对应。
        couple.parade_position = new_position
        couple.entrance_position = new_position
        self.gap_markers.setdefault(active.segment_id, []).append(
            GapMarker(couple_id, old_position, f"迟到跳序：{couple.couple_no:02d} 号顺延至队尾第 {new_position} 位")
        )
        self._log(at, actor, "late_skip", couple_id,
                  {"segment": active.segment_id, "from_position": old_position,
                   "to_position": new_position}, reason)
        return {"from_position": old_position, "to_position": new_position}

    def cancel_couple(self, couple_id: str, *, reason: str = "双方确认取消参加",
                      actor: str = "负责人", now: datetime | None = None) -> None:
        """双方取消：从所有未执行环节移除；已完成的签署与交付保留原记录。"""
        at = self._clock(now)
        couple = self._couple(couple_id)
        if couple.status == "cancelled":
            raise OrchestrationError("该对新人已取消")
        couple.status = "cancelled"
        couple.cancelled_at = at
        for asset in couple.assets:
            if asset.state in ("current",):
                asset.state = "withdrawn"
        for booking in couple.bookings:
            if booking.state == "booked":
                booking.state = "cancelled"
        cert = self._current_cert(couple)
        if cert is not None and not cert.signed:
            if cert.physical == "printed":
                cert.physical = "void"
                cert.electronic = "superseded"
            elif cert.electronic != "draft":
                cert.electronic = "superseded"
        self._log(at, actor, "cancel_couple", couple_id,
                  {"cert_retained": bool(cert and cert.signed),
                   "completed_records": [r.segment_id for r in self.records
                                         if r.couple_id == couple_id]}, reason)

    # ---------- 岗位视图（最小知情） ----------

    def staff_view(self, station: Station, *, now: datetime | None = None) -> dict:
        """只返回该岗位当前环节所需信息；环节未开始或已结束都拿不到内容。"""
        at = self._clock(now)
        kinds = STATION_SEGMENTS[station]
        active = self._active_segment()
        if active is None or active.kind not in kinds:
            raise OrchestrationError("当前不是该岗位环节，不提供资料")
        segment = active
        kind = active.kind
        done = self._records_for(segment.segment_id)
        plan = [p for p in self.plan_segment(kind) if p["item_id"] not in done]
        alerts: list[str] = []
        if any(not d.ok for d in self.devices.values() if d.kind == "screen"):
            faulty = [d.device_id for d in self.devices.values() if d.kind == "screen" and not d.ok]
            alerts.append(f"播放设备故障：{', '.join(faulty)}；未播项已改道或顺延")

        if station is Station.PARADE_MARSHAL:
            queue = [{"position": p["position"], "couple_no": p["couple_no"],
                      "names": p["names"],
                      "camera_avoid": p["do_not_display"]} for p in plan]
            markers = [m.position for m in self.gap_markers.get(segment.segment_id, [])]
            return {"station": station.value, "segment": segment.title, "at": at,
                    "queue": queue, "vacant_positions": markers, "alerts": alerts}
        if station is Station.USHER:
            return {"station": station.value, "segment": segment.title, "at": at, "alerts": alerts,
                    "queue": [{"position": p["position"], "couple_no": p["couple_no"],
                               "names": p["names"], "camera_avoid": p["do_not_display"]} for p in plan]}
        if station is Station.SCREEN_OPERATOR:
            queue = []
            if kind is SegmentKind.CERTIFICATE:
                # 影像环节因设备故障顺延的播放项，在下一个有屏环节优先补播。
                for p in self._media_plan([], deferred_only=True):
                    queue.append({"couple_no": p["couple_no"], "action": p["action"],
                                  "asset_id": p["asset_id"], "version": p["version"],
                                  "names": p["names"], "device_id": p["device_id"],
                                  "note": p["note"]})
                for p in plan:
                    if p["action"] == "withhold" or not p.get("show_electronic", True):
                        queue.append({"couple_no": p["couple_no"], "action": "suppress",
                                      "reason": p.get("note") or "电子婚书不公开"})
                    elif p["action"] == "block":
                        queue.append({"couple_no": p["couple_no"], "action": "hold",
                                      "reason": p["note"], "cert_version": p["cert_version"]})
                    else:
                        queue.append({"couple_no": p["couple_no"], "action": "show_electronic",
                                      "cert_version": p["cert_version"], "names": p["names"]})
            else:
                for p in plan:
                    if p["action"] == "suppress":
                        queue.append({"couple_no": p["couple_no"], "action": "suppress",
                                      "reason": p["reason"]})
                    elif p["action"] == "hold":
                        queue.append({"couple_no": p["couple_no"], "action": "hold", "reason": p["reason"]})
                    elif p["action"] == "defer":
                        queue.append({"couple_no": p["couple_no"], "action": "defer", "reason": p["reason"]})
                    else:
                        queue.append({"couple_no": p["couple_no"], "action": p["action"],
                                      "asset_id": p.get("asset_id"), "version": p.get("version"),
                                      "names": p["names"],
                                      "device_id": p.get("device_id"),
                                      "note": p.get("note", "")})
            return {"station": station.value, "segment": segment.title, "at": at, "alerts": alerts,
                    "queue": queue}
        if station is Station.RITUAL_OFFICER:
            return {"station": station.value, "segment": segment.title, "at": at, "alerts": alerts,
                    "queue": [{"couple_no": p["couple_no"], "names": p["names"], "role": p["role"],
                               "read_names_aloud": p["read_aloud"]} for p in plan]}
        if station is Station.CERT_STEWARD:
            queue = []
            for p in plan:
                queue.append({"couple_no": p["couple_no"], "names": p["names"],
                              "action": p["action"], "cert_version": p["cert_version"],
                              "physical": p["physical"], "electronic": p["electronic"],
                              "consistent": p["consistent"], "note": p["note"]})
            return {"station": station.value, "segment": segment.title, "at": at, "alerts": alerts,
                    "queue": queue}
        if station is Station.MARKET_DESK:
            return {"station": station.value, "segment": segment.title, "at": at, "alerts": alerts,
                    "queue": [{"code": p["code"], "service": p["service"], "slot": p["slot"]}
                              for p in plan]}
        raise OrchestrationError("未知岗位")

    # ---------- 新人自查视图 ----------

    def person_preview(self, person_id: str, *, now: datetime | None = None) -> dict:
        """新人确认自己将被怎样展示，并查看每次变更后的最新结果。"""
        at = self._clock(now)
        person = self._person(person_id)
        couple = self._couple_of(person_id)
        side = "A" if person_id == couple.person_a else "B"
        upcoming = []
        for segment in self.segments:
            if segment.status == "completed":
                continue
            if couple.status == "cancelled" and segment.status != "active":
                continue
            for item in self.plan_segment(segment.kind):
                if item.get("couple_id") == couple.couple_id:
                    upcoming.append({"segment": segment.title,
                                     "kind": segment.kind.value, **item})
        history = [
            {
                "segment": next(s.title for s in self.segments if s.segment_id == r.segment_id),
                "outcome": r.outcome, "at": r.at, "reason": r.reason,
                "shown_as": r.snapshot.get("names", {}),
                "media": r.snapshot.get("media"),
                "certificate": r.snapshot.get("certificate"),
            }
            for r in self.records if r.couple_id == couple.couple_id
        ]
        changes = [
            {"at": e.at, "action": e.action, "detail": e.detail, "reason": e.reason}
            for e in self.audit
            if e.target in (person_id, couple.couple_id)
        ]
        return {
            "at": at, "couple_no": couple.couple_no, "side": side,
            "status": couple.status, "display_name": person.display_name,
            "identity_verified": person.verified,
            "consents": {
                scope.value: {"active": consent.active,
                              "delete_due": consent.delete_due,
                              "revoked_at": consent.revoked_at}
                for scope, consent in person.consents.items()
            },
            "upcoming": upcoming, "history": history, "changes": changes,
        }

    # ---------- 负责人视图（不含证件资料） ----------

    def director_view(self, *, now: datetime | None = None) -> dict:
        at = self._clock(now)
        rows = []
        issues: list[str] = []
        for couple in sorted(self.couples.values(), key=lambda c: c.couple_no):
            consistent, cert_note = self.certificate_consistency(couple)
            persons = [self.persons[couple.person_a], self.persons[couple.person_b]]
            row = {
                "couple_no": couple.couple_no, "couple_id": couple.couple_id,
                "status": couple.status,
                "display_names": [p.display_name for p in persons],
                "identity_verified": [p.verified for p in persons],
                "consents": {
                    scope.value: [self._consent(p, scope).active for p in persons]
                    for scope in Scope
                },
                "attire_ready": [bool(p.attire_size) for p in persons],
                "parade_position": couple.parade_position,
                "entrance_position": couple.entrance_position,
                "order_aligned": couple.parade_position == couple.entrance_position,
                "role": couple.role,
                "media_current": self._current_asset(couple).asset_id
                if self._current_asset(couple) else None,
                "cert_version": couple.cert_current_no,
                "cert_consistent": consistent if couple.cert_current_no else None,
                "cert_note": cert_note,
                "bookings": [{"code": b.code, "service": b.service, "slot": b.slot,
                              "state": b.state} for b in couple.bookings],
            }
            rows.append(row)
            if couple.status == "cancelled":
                continue
            label = f"第{couple.couple_no:02d}对"
            if not all(p.verified for p in persons):
                issues.append(f"{label} 身份核验未完成")
            for scope in Scope:
                if not all(self._consent(p, scope).active for p in persons):
                    issues.append(f"{label} 缺少授权：{scope.value}")
            if not all(p.display_name for p in persons):
                issues.append(f"{label} 展示姓名未确认")
            if not all(p.attire_size for p in persons):
                issues.append(f"{label} 礼服尺寸未齐")
            if couple.parade_position is None or couple.entrance_position is None:
                issues.append(f"{label} 礼序位置未排定")
            elif not row["order_aligned"]:
                issues.append(f"{label} 巡游与入场顺序不一致")
            if self._current_asset(couple) is None and self._public_allowed(couple):
                issues.append(f"{label} 缺少影像素材")
            if couple.cert_current_no is None:
                issues.append(f"{label} 尚未发布电子婚书")
            elif not consistent:
                issues.append(f"{label} 婚书不一致：{cert_note}")

        progress = {}
        for segment in self.segments:
            if segment.kind is SegmentKind.MARKET:
                total = sum(1 for c in self._participating() for _ in c.bookings)
            else:
                total = sum(
                    1 for c in self._participating()
                    if segment.kind not in (SegmentKind.PARADE, SegmentKind.ENTRANCE)
                    or self._position_set(c, segment.kind)
                )
            done = len([r for r in self.records
                        if r.segment_id == segment.segment_id and r.outcome == "done"])
            skipped = len([r for r in self.records
                           if r.segment_id == segment.segment_id and r.outcome != "done"])
            progress[segment.kind.value] = {
                "status": segment.status, "done": done,
                "skipped_or_failed": skipped,
                "pending": max(total - done - skipped, 0),
            }

        deletions = self._scheduled_deletions()
        deletions.sort(key=lambda d: d["delete_due"])
        return {
            "at": at, "event_date": self.event_date,
            "total_couples": len(self.couples),
            "participating": len(self._participating()),
            "cancelled": sum(1 for c in self.couples.values() if c.status == "cancelled"),
            "rows": rows, "issues": issues, "progress": progress,
            "devices": [{"device_id": d.device_id, "kind": d.kind, "ok": d.ok}
                        for d in self.devices.values()],
            "scheduled_deletions": deletions,
        }

    @staticmethod
    def _position_set(couple: Couple, kind: SegmentKind) -> bool:
        if kind is SegmentKind.PARADE:
            return couple.parade_position is not None
        return couple.entrance_position is not None

    # ---------- 活动后到期删除 ----------

    def _scheduled_deletions(self) -> list[dict]:
        deletions = []
        for person in self.persons.values():
            for scope, consent in person.consents.items():
                if consent.granted and consent.purged_at is None and consent.delete_due is not None:
                    try:
                        couple = self._couple_of(person.person_id)
                        locator = f"第{couple.couple_no:02d}对-" + (
                            "A" if person.person_id == couple.person_a else "B")
                    except OrchestrationError:
                        locator = person.person_id
                    deletions.append({"who": locator, "scope": scope.value,
                                      "delete_due": consent.delete_due,
                                      "revoked": consent.revoked_at is not None})
        return deletions

    def deletions_due(self, as_of: date) -> list[dict]:
        """负责人核对哪些授权对应的资料已到删除期限。"""
        return [entry for entry in self._scheduled_deletions()
                if entry["delete_due"] <= as_of]

    def run_retention_deletions(self, as_of: date, *, now: datetime | None = None,
                                actor: str = "数据保护岗") -> list[dict]:
        """按授权到期日清除资料，保留删除事实与已签署婚书记录。"""
        at = self._clock(now)
        purged = []
        for person in list(self.persons.values()):
            for scope, consent in list(person.consents.items()):
                if (consent.granted and consent.purged_at is None
                        and consent.delete_due is not None
                        and consent.delete_due <= as_of):
                    removed = []
                    if scope is Scope.IDENTITY:
                        person.legal_name = None
                        person.id_doc_token = None
                        person.contact = None
                        removed = ["legal_name", "id_doc_token", "contact"]
                    elif scope is Scope.PUBLIC_DISPLAY:
                        couple = self._couple_of(person.person_id)
                        for asset in couple.assets:
                            if asset.state != "purged":
                                asset.asset_id = f"purged:v{asset.version}"
                                asset.state = "purged"
                        removed = ["media_files"]
                    elif scope is Scope.MARKET:
                        removed = ["market_contact"]
                    consent.purged_at = at
                    purged.append({"person_id": person.person_id, "scope": scope.value,
                                   "delete_due": consent.delete_due, "removed": removed})
                    self._log(at, actor, "purge_data", person.person_id,
                              {"scope": scope.value, "removed": removed})
        return purged
