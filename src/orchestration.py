"""青年集体婚礼编排服务。

在新人明确授权后管理身份核验、伴侣关系、展示姓名、影像素材、礼服尺寸、
仪式角色、巡游顺序、婚书版本与市集服务预约。撤回公开、替换素材、迟到跳序、
双方取消参加和设备故障会立即传播到尚未执行的环节；已经完成的签署与交付
保留原记录。新人可确认自己将如何被展示并查看变更结果，现场各岗位只获得
当前环节所需信息，负责人核对流程完整性、婚书一致性与按期删除清单时
接触不到证件资料。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum


class ConsentPurpose(str, Enum):
    """需要新人逐项授权的资料类别。"""

    IDENTITY_VERIFICATION = "身份核验"
    PARTNER_RELATION = "伴侣关系"
    DISPLAY_NAME = "展示姓名"
    MEDIA = "影像素材"
    DRESS_SIZE = "礼服尺寸"
    CEREMONY_ROLE = "仪式角色"
    PARADE_ORDER = "巡游顺序"
    CERTIFICATE = "婚书版本"
    MARKET = "市集服务预约"


class StageKind(str, Enum):
    """婚典现场环节，按执行顺序定义。"""

    DRESS_PREP = "礼服准备"
    PARADE = "巡游"
    ENTRANCE = "入场礼序"
    MEDIA_PLAYBACK = "影像播放"
    CERT_SIGNING = "婚书签署"
    MARKET = "市集服务"


class StageStatus(str, Enum):
    PENDING = "待执行"
    COMPLETED = "已完成"
    FAILED = "设备故障"


class StaffRole(str, Enum):
    """现场岗位，各自只对应一个环节。"""

    WARDROBE = "礼服管理"
    PARADE_MARSHAL = "巡游引导"
    MASTER_OF_CEREMONY = "司仪"
    MEDIA_OPERATOR = "影像播放员"
    CERT_OFFICER = "婚书司理"
    MARKET_STAFF = "市集服务台"


class ChangeKind(str, Enum):
    CONSENT_WITHDRAWN = "撤回公开"
    NAME_REVISED = "姓名修订"
    ASSET_REPLACED = "替换素材"
    LATE_ARRIVAL = "迟到跳序"
    COUPLE_CANCELLED = "双方取消参加"
    EQUIPMENT_FAILURE = "设备故障"


class Channel(str, Enum):
    ELECTRONIC = "电子婚书"
    PHYSICAL = "实体婚书"


# 除市集预约为自愿项目外，其余授权均为流程完整性的必要条件。
REQUIRED_CONSENTS = frozenset(p for p in ConsentPurpose if p is not ConsentPurpose.MARKET)

# 每类授权撤回后需要重新计算的环节。
_PURPOSE_STAGES = {
    ConsentPurpose.IDENTITY_VERIFICATION: frozenset(StageKind),
    ConsentPurpose.PARTNER_RELATION: frozenset({StageKind.ENTRANCE, StageKind.CERT_SIGNING}),
    ConsentPurpose.DISPLAY_NAME: frozenset(
        {StageKind.PARADE, StageKind.ENTRANCE, StageKind.MEDIA_PLAYBACK}
    ),
    ConsentPurpose.MEDIA: frozenset({StageKind.MEDIA_PLAYBACK}),
    ConsentPurpose.DRESS_SIZE: frozenset({StageKind.DRESS_PREP}),
    ConsentPurpose.CEREMONY_ROLE: frozenset({StageKind.ENTRANCE}),
    ConsentPurpose.PARADE_ORDER: frozenset({StageKind.PARADE, StageKind.ENTRANCE}),
    ConsentPurpose.CERTIFICATE: frozenset({StageKind.CERT_SIGNING}),
    ConsentPurpose.MARKET: frozenset({StageKind.MARKET}),
}

# 每个岗位只能看到自己所负责环节的计划。
_ROLE_STAGE = {
    StaffRole.WARDROBE: StageKind.DRESS_PREP,
    StaffRole.PARADE_MARSHAL: StageKind.PARADE,
    StaffRole.MASTER_OF_CEREMONY: StageKind.ENTRANCE,
    StaffRole.MEDIA_OPERATOR: StageKind.MEDIA_PLAYBACK,
    StaffRole.CERT_OFFICER: StageKind.CERT_SIGNING,
    StaffRole.MARKET_STAFF: StageKind.MARKET,
}


def _digest(names: tuple[str, str]) -> str:
    return hashlib.sha256("·".join(names).encode("utf-8")).hexdigest()[:12]


@dataclass
class Consent:
    purpose: ConsentPurpose
    granted_at: datetime
    delete_after: datetime
    withdrawn_at: datetime | None = None
    purged: bool = False

    @property
    def active(self) -> bool:
        return self.withdrawn_at is None and not self.purged


@dataclass
class MediaAsset:
    asset_id: str
    kind: str
    version: int
    approved: bool = False
    replaced_by: str | None = None


@dataclass
class CertificateVersion:
    version: int
    names: tuple[str, str]
    digest: str
    created_at: datetime


@dataclass
class CertificateChannelState:
    channel: Channel
    current: CertificateVersion | None = None
    signed: CertificateVersion | None = None
    delivered: CertificateVersion | None = None


@dataclass
class MarketBooking:
    booking_id: str
    service: str
    slot: str
    cancelled: bool = False


def _empty_channels() -> dict[Channel, CertificateChannelState]:
    return {channel: CertificateChannelState(channel) for channel in Channel}


@dataclass
class CoupleRecord:
    couple_id: str
    display_names: tuple[str, str]
    organization: str
    identity_verified: bool = False
    id_document_ref: str | None = None  # 证件资料，绝不进入任何视图
    partner_confirmed: bool = False
    names_confirmed: bool = False
    assets: dict[str, MediaAsset] = field(default_factory=dict)
    dress_sizes: dict[str, str] = field(default_factory=dict)
    ceremony_role: str | None = None
    parade_slot: int | None = None
    late: bool = False
    cancelled: bool = False
    consents: dict[ConsentPurpose, Consent] = field(default_factory=dict)
    certificates: dict[Channel, CertificateChannelState] = field(default_factory=_empty_channels)
    bookings: list[MarketBooking] = field(default_factory=list)
    cert_version_counter: int = 0


@dataclass
class Stage:
    kind: StageKind
    status: StageStatus = StageStatus.PENDING
    snapshot: list[dict] | None = None
    notices: list[str] = field(default_factory=list)
    completed_at: datetime | None = None


@dataclass
class ChangeEvent:
    seq: int
    kind: ChangeKind
    at: datetime
    couple_id: str | None
    detail: str
    affected_stages: tuple[StageKind, ...]


class WeddingOrchestrationService:
    """五十对新人集体婚礼的编排与隐私保护服务。"""

    def __init__(
        self,
        event_date: datetime,
        expected_couples: int = 50,
        retention_days: int = 30,
    ):
        self.event_date = event_date
        self.expected_couples = expected_couples
        self.retention_days = retention_days
        self._couples: dict[str, CoupleRecord] = {}
        self._stages: dict[StageKind, Stage] = {kind: Stage(kind) for kind in StageKind}
        self._events: list[ChangeEvent] = []
        self._booking_counter = 0

    # ---------- 登记与授权 ----------

    def register_couple(
        self, couple_id: str, name_a: str, name_b: str, organization: str
    ) -> None:
        if couple_id in self._couples:
            raise ValueError("新人编号已存在")
        if len(self._couples) >= self.expected_couples:
            raise ValueError("已达活动新人对数上限")
        self._couples[couple_id] = CoupleRecord(couple_id, (name_a, name_b), organization)

    def grant_consent(self, couple_id: str, purpose: ConsentPurpose, at: datetime) -> None:
        couple = self._couple(couple_id)
        couple.consents[purpose] = Consent(
            purpose, at, self.event_date + timedelta(days=self.retention_days)
        )
        if purpose is ConsentPurpose.CERTIFICATE:
            self._bump_certificate(couple, at)

    def withdraw_consent(
        self, couple_id: str, purpose: ConsentPurpose, at: datetime
    ) -> ChangeEvent:
        couple = self._couple(couple_id)
        consent = couple.consents.get(purpose)
        if consent is None or not consent.active:
            raise ValueError("授权不存在或已撤回")
        consent.withdrawn_at = at
        if purpose is ConsentPurpose.IDENTITY_VERIFICATION:
            # 核验授权撤回后，证件资料立即清除，新人退出尚未执行的环节。
            couple.identity_verified = False
            couple.id_document_ref = None
        if purpose is ConsentPurpose.CERTIFICATE:
            self._void_unsigned_certificates(couple)
        if purpose is ConsentPurpose.MARKET:
            for booking in couple.bookings:
                booking.cancelled = True
        return self._emit(
            ChangeKind.CONSENT_WITHDRAWN,
            at,
            couple_id,
            f"撤回{purpose.value}授权",
            _PURPOSE_STAGES[purpose],
        )

    def verify_identity(self, couple_id: str, id_document_ref: str) -> None:
        couple = self._couple(couple_id)
        self._require_consent(couple, ConsentPurpose.IDENTITY_VERIFICATION)
        couple.identity_verified = True
        couple.id_document_ref = id_document_ref

    def confirm_partner_relation(self, couple_id: str) -> None:
        couple = self._couple(couple_id)
        self._require_consent(couple, ConsentPurpose.PARTNER_RELATION)
        couple.partner_confirmed = True

    def confirm_display_names(self, couple_id: str, at: datetime) -> None:
        """新人确认展示姓名，确认前各环节只显示匿名编号。"""
        couple = self._couple(couple_id)
        self._require_consent(couple, ConsentPurpose.DISPLAY_NAME)
        couple.names_confirmed = True
        self._bump_certificate(couple, at)

    def revise_display_names(
        self, couple_id: str, name_a: str, name_b: str, at: datetime
    ) -> ChangeEvent:
        couple = self._couple(couple_id)
        self._require_consent(couple, ConsentPurpose.DISPLAY_NAME)
        couple.display_names = (name_a, name_b)
        couple.names_confirmed = True
        self._bump_certificate(couple, at)
        return self._emit(
            ChangeKind.NAME_REVISED,
            at,
            couple_id,
            f"展示姓名修订为{name_a}·{name_b}",
            _PURPOSE_STAGES[ConsentPurpose.DISPLAY_NAME] | {StageKind.CERT_SIGNING},
        )

    # ---------- 素材、礼服、角色与顺序 ----------

    def add_media_asset(self, couple_id: str, asset_id: str, kind: str) -> None:
        couple = self._couple(couple_id)
        self._require_consent(couple, ConsentPurpose.MEDIA)
        couple.assets[asset_id] = MediaAsset(asset_id, kind, version=1)

    def approve_media_asset(self, couple_id: str, asset_id: str) -> None:
        """新人确认素材后才会进入播放单。"""
        couple = self._couple(couple_id)
        couple.assets[asset_id].approved = True

    def replace_media_asset(
        self, couple_id: str, old_id: str, new_id: str, kind: str, at: datetime
    ) -> ChangeEvent:
        couple = self._couple(couple_id)
        self._require_consent(couple, ConsentPurpose.MEDIA)
        old = couple.assets.get(old_id)
        if old is None or old.replaced_by is not None:
            raise ValueError("原素材不存在或已被替换")
        old.replaced_by = new_id
        # 新人主动发起替换，视为对新素材的确认。
        couple.assets[new_id] = MediaAsset(new_id, kind, old.version + 1, approved=True)
        return self._emit(
            ChangeKind.ASSET_REPLACED,
            at,
            couple_id,
            f"素材{old_id}替换为{new_id}",
            _PURPOSE_STAGES[ConsentPurpose.MEDIA],
        )

    def set_dress_sizes(self, couple_id: str, sizes: dict[str, str]) -> None:
        couple = self._couple(couple_id)
        self._require_consent(couple, ConsentPurpose.DRESS_SIZE)
        couple.dress_sizes = dict(sizes)

    def assign_ceremony_role(self, couple_id: str, role: str) -> None:
        couple = self._couple(couple_id)
        self._require_consent(couple, ConsentPurpose.CEREMONY_ROLE)
        couple.ceremony_role = role

    def set_parade_order(self, ordered_ids: list[str]) -> None:
        if set(ordered_ids) != set(self._couples):
            raise ValueError("巡游顺序必须覆盖全部已登记新人")
        for couple_id in ordered_ids:
            self._require_consent(self._couples[couple_id], ConsentPurpose.PARADE_ORDER)
        for slot, couple_id in enumerate(ordered_ids, 1):
            self._couples[couple_id].parade_slot = slot

    # ---------- 婚书 ----------

    def sign_certificate(self, couple_id: str, channel: Channel) -> None:
        """签署即冻结该渠道版本，之后的姓名修订只产生新版本。"""
        couple = self._couple(couple_id)
        self._require_consent(couple, ConsentPurpose.CERTIFICATE)
        state = couple.certificates[channel]
        if state.current is None:
            raise ValueError("婚书尚未生成")
        state.signed = state.current

    def deliver_certificate(self, couple_id: str, channel: Channel) -> None:
        state = self._couple(couple_id).certificates[channel]
        if state.signed is None:
            raise ValueError("需先签署再交付")
        state.delivered = state.signed

    # ---------- 市集服务预约 ----------

    def book_market_service(self, couple_id: str, service: str, slot: str) -> str:
        couple = self._couple(couple_id)
        if couple.cancelled:
            raise ValueError("已取消参加，不能预约")
        self._require_consent(couple, ConsentPurpose.MARKET)
        self._booking_counter += 1
        booking = MarketBooking(f"BK-{self._booking_counter:03d}", service, slot)
        couple.bookings.append(booking)
        return booking.booking_id

    # ---------- 现场变更 ----------

    def mark_late(self, couple_id: str, at: datetime) -> ChangeEvent:
        couple = self._couple(couple_id)
        if couple.cancelled:
            raise ValueError("已取消参加")
        couple.late = True
        return self._emit(
            ChangeKind.LATE_ARRIVAL,
            at,
            couple_id,
            "迟到，巡游与入场顺次后移",
            frozenset({StageKind.PARADE, StageKind.ENTRANCE}),
        )

    def cancel_participation(
        self, couple_id: str, both_confirmed: bool, at: datetime
    ) -> ChangeEvent:
        couple = self._couple(couple_id)
        if not both_confirmed:
            raise ValueError("须双方共同确认取消")
        if couple.cancelled:
            raise ValueError("已取消参加")
        couple.cancelled = True
        self._void_unsigned_certificates(couple)
        for booking in couple.bookings:
            booking.cancelled = True
        return self._emit(
            ChangeKind.COUPLE_CANCELLED,
            at,
            couple_id,
            "双方取消参加，未执行环节全部移除",
            frozenset(StageKind),
        )

    def report_equipment_failure(
        self, stage_kind: StageKind, description: str, at: datetime
    ) -> ChangeEvent:
        stage = self._stages[stage_kind]
        if stage.status is StageStatus.COMPLETED:
            raise ValueError("环节已完成，不受故障影响")
        stage.status = StageStatus.FAILED
        for item in self._stages.values():
            if item.status is not StageStatus.COMPLETED:
                item.notices.append(f"设备故障通报：{description}")
        return self._emit(
            ChangeKind.EQUIPMENT_FAILURE,
            at,
            None,
            f"{stage_kind.value}设备故障：{description}",
            frozenset(StageKind),
        )

    def resolve_equipment_failure(self, stage_kind: StageKind) -> None:
        stage = self._stages[stage_kind]
        if stage.status is StageStatus.FAILED:
            stage.status = StageStatus.PENDING

    # ---------- 环节执行 ----------

    def complete_stage(self, stage_kind: StageKind, at: datetime) -> list[dict]:
        """执行环节并冻结快照，之后的变更不再影响该环节。"""
        stage = self._stages[stage_kind]
        if stage.status is StageStatus.COMPLETED:
            raise ValueError("环节已完成")
        if stage.status is StageStatus.FAILED:
            raise ValueError("设备故障未排除，不能执行")
        stage.snapshot = self._build_plan(stage_kind)
        stage.status = StageStatus.COMPLETED
        stage.completed_at = at
        return [dict(entry) for entry in stage.snapshot]

    def stage_status(self, stage_kind: StageKind) -> StageStatus:
        return self._stages[stage_kind].status

    def stage_notices(self, stage_kind: StageKind) -> list[str]:
        return list(self._stages[stage_kind].notices)

    # ---------- 视图 ----------

    def stage_plan(self, stage_kind: StageKind) -> list[dict]:
        """环节计划：已完成环节返回冻结快照，其余返回实时计划。"""
        stage = self._stages[stage_kind]
        if stage.status is StageStatus.COMPLETED:
            return [dict(entry) for entry in stage.snapshot or []]
        return self._build_plan(stage_kind)

    def staff_view(self, role: StaffRole) -> list[dict]:
        """岗位视角：只含该岗位所负责环节执行所需的最少信息。"""
        return self.stage_plan(_ROLE_STAGE[role])

    def couple_view(self, couple_id: str) -> dict:
        """新人视角：自己将如何被展示，以及影响自己的变更结果。"""
        couple = self._couple(couple_id)
        upcoming = {}
        if self._participating(couple):
            for kind in StageKind:
                if self._stages[kind].status is StageStatus.PENDING:
                    for entry in self._build_plan(kind):
                        if entry["couple_id"] == couple_id:
                            upcoming[kind.value] = entry
        changes = [
            {
                "seq": event.seq,
                "kind": event.kind.value,
                "at": event.at.isoformat(),
                "detail": event.detail,
                "affected_stages": [kind.value for kind in event.affected_stages],
            }
            for event in self._events
            if event.couple_id is None or event.couple_id == couple_id
        ]
        return {
            "couple_id": couple.couple_id,
            "display_names": couple.display_names,
            "display_label": self._display_label(couple),
            "names_confirmed": couple.names_confirmed,
            "identity_verified": couple.identity_verified,
            "parade_slot": couple.parade_slot,
            "late": couple.late,
            "cancelled": couple.cancelled,
            "ceremony_role": couple.ceremony_role,
            "assets": [
                {
                    "asset_id": asset.asset_id,
                    "kind": asset.kind,
                    "version": asset.version,
                    "approved": asset.approved,
                    "replaced_by": asset.replaced_by,
                }
                for asset in couple.assets.values()
            ],
            "certificates": self._certificate_brief(couple),
            "market_bookings": [
                {
                    "booking_id": booking.booking_id,
                    "service": booking.service,
                    "slot": booking.slot,
                    "cancelled": booking.cancelled,
                }
                for booking in couple.bookings
            ],
            "upcoming_stages": upcoming,
            "changes": changes,
        }

    def audit_overview(self) -> dict:
        """负责人核对：流程完整性、婚书一致性，不含任何证件资料。"""
        couples = []
        for couple in self._couples.values():
            checklist = self._checklist(couple)
            consistent, notes = self._certificate_consistency(couple)
            couples.append(
                {
                    "couple_id": couple.couple_id,
                    "cancelled": couple.cancelled,
                    "checklist": checklist,
                    "complete": all(checklist.values()),
                    "certificate_consistent": consistent,
                    "certificate_notes": notes,
                }
            )
        active = [item for item in couples if not item["cancelled"]]
        return {
            "expected_couples": self.expected_couples,
            "registered_couples": len(self._couples),
            "all_registered": len(self._couples) == self.expected_couples,
            "all_complete": bool(active) and all(item["complete"] for item in active),
            "certificates_all_consistent": all(
                item["certificate_consistent"] for item in active
            ),
            "couples": couples,
        }

    def retention_report(self, at: datetime) -> list[dict]:
        """按期删除清单：每项授权的计划删除时间与是否到期。"""
        items = []
        for couple in self._couples.values():
            if couple.id_document_ref is not None:
                delete_after = self.event_date + timedelta(days=self.retention_days)
                items.append(
                    {
                        "couple_id": couple.couple_id,
                        "item": "证件资料",
                        "delete_after": delete_after.isoformat(),
                        "withdrawn": False,
                        "due": at >= delete_after,
                    }
                )
            for consent in couple.consents.values():
                if consent.purged:
                    continue
                items.append(
                    {
                        "couple_id": couple.couple_id,
                        "item": consent.purpose.value,
                        "delete_after": consent.delete_after.isoformat(),
                        "withdrawn": consent.withdrawn_at is not None,
                        "due": consent.withdrawn_at is not None or at >= consent.delete_after,
                    }
                )
        return items

    def purge_expired(self, at: datetime) -> list[str]:
        """删除到期或已撤回授权对应的资料；已签署与已交付的婚书保留原记录。"""
        purged = []
        for couple in self._couples.values():
            for consent in couple.consents.values():
                if consent.purged:
                    continue
                due = consent.withdrawn_at is not None or at >= consent.delete_after
                if not due:
                    continue
                self._purge_data(couple, consent.purpose)
                consent.purged = True
                purged.append(f"{couple.couple_id}:{consent.purpose.value}")
        return purged

    # ---------- 内部 ----------

    def _couple(self, couple_id: str) -> CoupleRecord:
        try:
            return self._couples[couple_id]
        except KeyError:
            raise ValueError("新人未登记") from None

    def _consent_active(self, couple: CoupleRecord, purpose: ConsentPurpose) -> bool:
        consent = couple.consents.get(purpose)
        return consent is not None and consent.active

    def _require_consent(self, couple: CoupleRecord, purpose: ConsentPurpose) -> None:
        if not self._consent_active(couple, purpose):
            raise ValueError(f"缺少{purpose.value}授权")

    def _participating(self, couple: CoupleRecord) -> bool:
        return not couple.cancelled and couple.identity_verified

    def _ordered_couples(self) -> list[CoupleRecord]:
        couples = [c for c in self._couples.values() if self._participating(c)]
        on_time = sorted(
            (c for c in couples if not c.late), key=lambda c: c.parade_slot or 999
        )
        late = sorted((c for c in couples if c.late), key=lambda c: c.parade_slot or 999)
        return on_time + late  # 迟到跳序：迟到者移到队尾

    def _display_label(self, couple: CoupleRecord) -> str:
        if self._consent_active(couple, ConsentPurpose.DISPLAY_NAME) and couple.names_confirmed:
            return "·".join(couple.display_names)
        if couple.parade_slot is not None:
            return f"新人{couple.parade_slot:02d}号"
        return f"新人编号{couple.couple_id}"

    def _build_plan(self, kind: StageKind) -> list[dict]:
        entries = []
        for seq, couple in enumerate(self._ordered_couples(), 1):
            entry = {
                "couple_id": couple.couple_id,
                "seq": seq,
                "display": self._display_label(couple),
            }
            if couple.late:
                entry["deferred"] = True
            if kind is StageKind.DRESS_PREP:
                entry["dress_sizes"] = (
                    dict(couple.dress_sizes)
                    if self._consent_active(couple, ConsentPurpose.DRESS_SIZE)
                    else {}
                )
            elif kind is StageKind.ENTRANCE:
                entry["role"] = (
                    couple.ceremony_role
                    if self._consent_active(couple, ConsentPurpose.CEREMONY_ROLE)
                    else None
                )
            elif kind is StageKind.MEDIA_PLAYBACK:
                entry["assets"] = self._approved_assets(couple)
            elif kind is StageKind.CERT_SIGNING:
                entry["certificate"] = self._certificate_brief(couple)
            elif kind is StageKind.MARKET:
                entry["bookings"] = [
                    {"booking_id": b.booking_id, "service": b.service, "slot": b.slot}
                    for b in couple.bookings
                    if not b.cancelled
                ]
            entries.append(entry)
        return entries

    def _approved_assets(self, couple: CoupleRecord) -> list[dict]:
        # 影像上大屏需要素材与公开展示双重授权，撤回任一即撤下。
        if not (
            self._consent_active(couple, ConsentPurpose.MEDIA)
            and self._consent_active(couple, ConsentPurpose.DISPLAY_NAME)
        ):
            return []
        return [
            {"asset_id": a.asset_id, "kind": a.kind, "version": a.version}
            for a in couple.assets.values()
            if a.approved and a.replaced_by is None
        ]

    def _certificate_brief(self, couple: CoupleRecord) -> dict:
        brief = {}
        for channel, state in couple.certificates.items():
            brief[channel.value] = {
                "version": state.current.version if state.current else None,
                "digest": state.current.digest if state.current else None,
                "signed": state.signed is not None,
                "delivered": state.delivered is not None,
            }
        return brief

    def _bump_certificate(self, couple: CoupleRecord, at: datetime) -> None:
        if not self._consent_active(couple, ConsentPurpose.CERTIFICATE):
            return
        couple.cert_version_counter += 1
        version = CertificateVersion(
            couple.cert_version_counter, couple.display_names, _digest(couple.display_names), at
        )
        for state in couple.certificates.values():
            state.current = version

    def _void_unsigned_certificates(self, couple: CoupleRecord) -> None:
        for state in couple.certificates.values():
            if state.signed is None:
                state.current = None

    def _checklist(self, couple: CoupleRecord) -> dict[str, bool]:
        return {
            "身份已核验": couple.identity_verified,
            "伴侣关系已确认": couple.partner_confirmed,
            "授权齐全": all(
                self._consent_active(couple, purpose) for purpose in REQUIRED_CONSENTS
            ),
            "展示姓名已确认": couple.names_confirmed,
            "影像素材已确认": any(
                a.approved and a.replaced_by is None for a in couple.assets.values()
            ),
            "礼服尺寸已报": bool(couple.dress_sizes),
            "仪式角色已排": couple.ceremony_role is not None,
            "巡游顺序已排": couple.parade_slot is not None,
            "婚书已生成": all(
                state.current is not None for state in couple.certificates.values()
            ),
        }

    def _certificate_consistency(self, couple: CoupleRecord) -> tuple[bool, list[str]]:
        electronic = couple.certificates[Channel.ELECTRONIC]
        physical = couple.certificates[Channel.PHYSICAL]
        if electronic.current is None or physical.current is None:
            return False, ["婚书尚未生成"]
        consistent = True
        notes = []
        if electronic.current.digest != physical.current.digest:
            consistent = False
            notes.append("电子与实体婚书当前版本不一致")
        for label, state in (("电子婚书", electronic), ("实体婚书", physical)):
            if state.signed is not None and state.signed.digest != state.current.digest:
                consistent = False
                notes.append(f"{label}签署后姓名已修订，需重新签署")
            if state.delivered is not None and state.delivered.digest != state.current.digest:
                consistent = False
                notes.append(f"{label}交付后姓名已修订，需按新版本重印")
        return consistent, notes

    def _purge_data(self, couple: CoupleRecord, purpose: ConsentPurpose) -> None:
        if purpose is ConsentPurpose.IDENTITY_VERIFICATION:
            couple.id_document_ref = None
            couple.identity_verified = False
        elif purpose is ConsentPurpose.MEDIA:
            couple.assets.clear()
        elif purpose is ConsentPurpose.DRESS_SIZE:
            couple.dress_sizes.clear()
        elif purpose is ConsentPurpose.DISPLAY_NAME:
            couple.names_confirmed = False
        elif purpose is ConsentPurpose.CEREMONY_ROLE:
            couple.ceremony_role = None
        elif purpose is ConsentPurpose.PARADE_ORDER:
            couple.parade_slot = None
        elif purpose is ConsentPurpose.CERTIFICATE:
            self._void_unsigned_certificates(couple)
        elif purpose is ConsentPurpose.MARKET:
            couple.bookings.clear()
        elif purpose is ConsentPurpose.PARTNER_RELATION:
            couple.partner_confirmed = False

    def _emit(
        self,
        kind: ChangeKind,
        at: datetime,
        couple_id: str | None,
        detail: str,
        relevant: frozenset[StageKind],
    ) -> ChangeEvent:
        affected = tuple(
            stage_kind
            for stage_kind in StageKind
            if stage_kind in relevant
            and self._stages[stage_kind].status is StageStatus.PENDING
        )
        event = ChangeEvent(len(self._events) + 1, kind, at, couple_id, detail, affected)
        self._events.append(event)
        return event
