import unittest
from datetime import datetime, timedelta

from src.orchestration import (
    ChangeKind,
    Channel,
    ConsentPurpose,
    StageKind,
    StageStatus,
    StaffRole,
    WeddingOrchestrationService,
)

T0 = datetime(2026, 9, 27, 8, 0)
EVENT_DATE = datetime(2026, 10, 3, 9, 0)


class OrchestrationTest(unittest.TestCase):
    def setUp(self):
        self.svc = WeddingOrchestrationService(event_date=EVENT_DATE)

    def _onboard(self, cid, name_a, name_b):
        svc = self.svc
        svc.register_couple(cid, name_a, name_b, "某高校")
        for purpose in ConsentPurpose:
            svc.grant_consent(cid, purpose, T0)
        svc.verify_identity(cid, f"IDREF-{cid}")
        svc.confirm_partner_relation(cid)
        svc.confirm_display_names(cid, T0)
        svc.add_media_asset(cid, f"A-{cid}", "照片")
        svc.approve_media_asset(cid, f"A-{cid}")
        svc.set_dress_sizes(cid, {"一人": "L", "另一人": "M"})
        svc.assign_ceremony_role(cid, "却扇礼")

    def _onboard_many(self, n):
        ids = [f"C-{i:03d}" for i in range(1, n + 1)]
        for i, cid in enumerate(ids, 1):
            self._onboard(cid, f"甲{i}", f"乙{i}")
        self.svc.set_parade_order(ids)
        return ids

    def test_full_onboarding_completes_audit(self):
        self._onboard_many(2)
        audit = self.svc.audit_overview()
        self.assertTrue(audit["all_complete"])
        self.assertTrue(audit["certificates_all_consistent"])
        self.assertFalse(audit["all_registered"])  # 只登记 2 对，未到 50 对

    def test_registration_stops_at_capacity(self):
        svc = WeddingOrchestrationService(event_date=EVENT_DATE, expected_couples=1)
        svc.register_couple("C-1", "甲", "乙", "某企业")
        with self.assertRaises(ValueError):
            svc.register_couple("C-2", "丙", "丁", "某科研院所")

    def test_withdraw_public_display_reaches_only_pending_stages(self):
        ids = self._onboard_many(2)
        snapshot = self.svc.complete_stage(StageKind.PARADE, T0)
        self.assertEqual(snapshot[0]["display"], "甲1·乙1")

        event = self.svc.withdraw_consent(
            ids[0], ConsentPurpose.DISPLAY_NAME, T0 + timedelta(hours=1)
        )

        # 传播到尚未执行的相关环节，已完成的巡游不在其中
        self.assertNotIn(StageKind.PARADE, event.affected_stages)
        self.assertIn(StageKind.ENTRANCE, event.affected_stages)
        self.assertIn(StageKind.MEDIA_PLAYBACK, event.affected_stages)
        # 已完成的巡游保留原记录
        self.assertEqual(
            self.svc.stage_plan(StageKind.PARADE)[0]["display"], "甲1·乙1"
        )
        # 尚未执行的入场环节已匿名化
        entrance = self.svc.staff_view(StaffRole.MASTER_OF_CEREMONY)
        self.assertEqual(entrance[0]["display"], "新人01号")
        self.assertEqual(entrance[1]["display"], "甲2·乙2")
        # 大屏播放单撤下其影像
        media = self.svc.staff_view(StaffRole.MEDIA_OPERATOR)
        self.assertEqual(media[0]["assets"], [])
        self.assertEqual(len(media[1]["assets"]), 1)
        # 新人可查看自己将如何被展示以及变更结果
        view = self.svc.couple_view(ids[0])
        self.assertEqual(
            view["upcoming_stages"][StageKind.ENTRANCE.value]["display"], "新人01号"
        )
        self.assertEqual(view["changes"][-1]["kind"], ChangeKind.CONSENT_WITHDRAWN.value)

    def test_identity_withdrawal_removes_couple_from_pending_plans(self):
        ids = self._onboard_many(2)
        self.svc.withdraw_consent(ids[0], ConsentPurpose.IDENTITY_VERIFICATION, T0)
        plan = self.svc.staff_view(StaffRole.PARADE_MARSHAL)
        self.assertEqual([entry["couple_id"] for entry in plan], [ids[1]])

    def test_replace_media_asset_updates_pending_playback(self):
        ids = self._onboard_many(1)
        event = self.svc.replace_media_asset(ids[0], f"A-{ids[0]}", "A-new", "视频", T0)
        self.assertEqual(event.kind, ChangeKind.ASSET_REPLACED)
        self.assertEqual(list(event.affected_stages), [StageKind.MEDIA_PLAYBACK])
        media = self.svc.staff_view(StaffRole.MEDIA_OPERATOR)
        self.assertEqual([a["asset_id"] for a in media[0]["assets"]], ["A-new"])

    def test_late_arrival_moves_couple_to_the_end(self):
        ids = self._onboard_many(3)
        event = self.svc.mark_late(ids[1], T0)
        self.assertEqual(
            set(event.affected_stages), {StageKind.PARADE, StageKind.ENTRANCE}
        )
        plan = self.svc.staff_view(StaffRole.PARADE_MARSHAL)
        self.assertEqual(
            [entry["couple_id"] for entry in plan], [ids[0], ids[2], ids[1]]
        )
        self.assertTrue(plan[-1]["deferred"])

    def test_cancel_requires_both_and_keeps_signed_records(self):
        ids = self._onboard_many(2)
        cid = ids[0]
        self.svc.book_market_service(cid, "茶点", "10:00")
        self.svc.sign_certificate(cid, Channel.ELECTRONIC)

        with self.assertRaises(ValueError):
            self.svc.cancel_participation(cid, both_confirmed=False, at=T0)

        event = self.svc.cancel_participation(cid, both_confirmed=True, at=T0)
        self.assertEqual(set(event.affected_stages), set(StageKind))
        # 未执行环节全部移除
        remaining = self.svc.staff_view(StaffRole.PARADE_MARSHAL)
        self.assertEqual([entry["couple_id"] for entry in remaining], [ids[1]])
        view = self.svc.couple_view(cid)
        self.assertEqual(view["upcoming_stages"], {})
        # 市集预约取消
        self.assertTrue(view["market_bookings"][0]["cancelled"])
        # 已签署的电子婚书保留原记录，未签署的实体婚书作废
        self.assertTrue(view["certificates"][Channel.ELECTRONIC.value]["signed"])
        self.assertIsNone(view["certificates"][Channel.PHYSICAL.value]["version"])

    def test_equipment_failure_blocks_stage_and_notifies_pending(self):
        self._onboard_many(2)
        self.svc.complete_stage(StageKind.PARADE, T0)
        event = self.svc.report_equipment_failure(
            StageKind.MEDIA_PLAYBACK, "大屏黑屏", T0
        )
        # 已完成与故障环节本身都不是传播对象
        self.assertNotIn(StageKind.PARADE, event.affected_stages)
        self.assertNotIn(StageKind.MEDIA_PLAYBACK, event.affected_stages)
        self.assertIn(StageKind.ENTRANCE, event.affected_stages)
        self.assertEqual(
            self.svc.stage_status(StageKind.MEDIA_PLAYBACK), StageStatus.FAILED
        )
        with self.assertRaises(ValueError):
            self.svc.complete_stage(StageKind.MEDIA_PLAYBACK, T0)
        self.assertTrue(self.svc.stage_notices(StageKind.ENTRANCE))
        self.svc.resolve_equipment_failure(StageKind.MEDIA_PLAYBACK)
        self.assertEqual(
            self.svc.stage_status(StageKind.MEDIA_PLAYBACK), StageStatus.PENDING
        )

    def test_name_revision_flags_physical_certificate_reprint(self):
        ids = self._onboard_many(1)
        cid = ids[0]
        self.svc.sign_certificate(cid, Channel.PHYSICAL)
        self.svc.deliver_certificate(cid, Channel.PHYSICAL)

        self.svc.revise_display_names(cid, "甲新", "乙新", T0)

        audit = self.svc.audit_overview()
        entry = audit["couples"][0]
        self.assertFalse(entry["certificate_consistent"])
        self.assertFalse(audit["certificates_all_consistent"])
        self.assertTrue(any("重印" in note for note in entry["certificate_notes"]))
        # 电子婚书未交付，当前版本已是最新姓名
        view = self.svc.couple_view(cid)
        self.assertEqual(view["display_names"], ("甲新", "乙新"))
        self.assertFalse(view["certificates"][Channel.ELECTRONIC.value]["delivered"])

    def test_staff_views_expose_only_current_stage_needs(self):
        self._onboard_many(2)
        parade = self.svc.staff_view(StaffRole.PARADE_MARSHAL)
        self.assertEqual(set(parade[0]), {"couple_id", "seq", "display"})
        wardrobe = self.svc.staff_view(StaffRole.WARDROBE)
        self.assertIn("dress_sizes", wardrobe[0])
        media = self.svc.staff_view(StaffRole.MEDIA_OPERATOR)
        self.assertIn("assets", media[0])
        cert = self.svc.staff_view(StaffRole.CERT_OFFICER)
        self.assertIn("certificate", cert[0])
        for role in StaffRole:
            view = str(self.svc.staff_view(role))
            self.assertNotIn("IDREF", view)
            self.assertNotIn("某高校", view)

    def test_audit_overview_contains_no_id_documents(self):
        self._onboard_many(2)
        audit = self.svc.audit_overview()
        self.assertNotIn("IDREF", str(audit))
        checklist = audit["couples"][0]["checklist"]
        self.assertTrue(all(checklist.values()))

    def test_retention_report_and_scheduled_purge(self):
        ids = self._onboard_many(1)
        cid = ids[0]
        self.svc.sign_certificate(cid, Channel.ELECTRONIC)

        before = self.svc.retention_report(EVENT_DATE + timedelta(days=15))
        self.assertTrue(before)
        self.assertFalse(any(item["due"] for item in before))

        after = self.svc.retention_report(EVENT_DATE + timedelta(days=31))
        self.assertTrue(all(item["due"] for item in after))

        purged = self.svc.purge_expired(EVENT_DATE + timedelta(days=31))
        self.assertTrue(purged)
        view = self.svc.couple_view(cid)
        self.assertEqual(view["assets"], [])
        self.assertFalse(view["identity_verified"])
        # 已签署的婚书保留原记录
        self.assertTrue(view["certificates"][Channel.ELECTRONIC.value]["signed"])

    def test_withdrawn_consent_is_due_for_deletion_immediately(self):
        ids = self._onboard_many(1)
        self.svc.withdraw_consent(ids[0], ConsentPurpose.MEDIA, T0)
        report = self.svc.retention_report(T0)
        media = [item for item in report if item["item"] == ConsentPurpose.MEDIA.value]
        self.assertTrue(media[0]["due"])
        self.assertTrue(media[0]["withdrawn"])


if __name__ == "__main__":
    unittest.main()
