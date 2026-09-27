import unittest
from datetime import date, datetime, timedelta

from src.orchestration import (
    Booking,
    OrchestrationError,
    Scope,
    SegmentKind,
    Station,
    WeddingEvent,
)

EVENT_DAY = date(2026, 10, 18)
T0 = datetime(2026, 10, 18, 9, 0)


def build_event(*, couples: int = 3, now: datetime | None = None) -> WeddingEvent:
    """构造一个已完成登记、四类授权、核验、配对、排位置和素材提交的小型婚典。"""
    event = WeddingEvent(EVENT_DAY, now=now or T0)
    event.register_device("screen-main")
    event.register_device("screen-backup")
    for i in range(1, couples + 1):
        a = f"p{i}a"
        b = f"p{i}b"
        event.register_person(a, "高校" if i % 2 else "企业")
        event.register_person(b, "科研院所")
        for pid in (a, b):
            event.grant_consent(pid, Scope.IDENTITY)
            event.verify_identity(pid, f"姓名{i}{'甲' if pid.endswith('a') else '乙'}", f"token-{pid}")
            event.grant_consent(pid, Scope.PUBLIC_DISPLAY)
            event.grant_consent(pid, Scope.CERTIFICATE)
            event.grant_consent(pid, Scope.MARKET)
            event.set_display_name(pid, f"展示{i}{'A' if pid.endswith('a') else 'B'}")
            event.set_attire(pid, "M")
        event.pair_couple(f"c{i}", a, b)
        event.set_position(f"c{i}", i)
        event.submit_media(f"c{i}", f"asset-{i}-v1")
    return event


def approve_and_print(event: WeddingEvent, *couple_ids: str) -> None:
    for cid in couple_ids:
        event.approve_certificate(cid)
        event.print_certificate(cid)


class SetupTest(unittest.TestCase):
    def test_identity_verification_requires_consent(self):
        event = WeddingEvent(EVENT_DAY)
        event.register_person("p1a", "高校")
        with self.assertRaises(OrchestrationError):
            event.verify_identity("p1a", "法定名", "token")

    def test_pairing_requires_both_verified(self):
        event = build_event(couples=1)
        event.register_person("x", "企业")
        event.grant_consent("x", Scope.IDENTITY)
        with self.assertRaises(OrchestrationError):
            event.pair_couple("cx", "p1a", "x")

    def test_position_unique_and_aligned(self):
        event = build_event(couples=2)
        with self.assertRaises(OrchestrationError):
            event.set_position("c2", 1)
        c1 = event.couples["c1"]
        self.assertEqual(c1.parade_position, c1.entrance_position)

    def test_full_day_flow_for_three_couples(self):
        event = build_event(couples=3)
        approve_and_print(event, "c1", "c2", "c3")
        event.add_market_service("非遗纪念照", "14:00", 2)
        event.book_market("c1", "非遗纪念照", "14:00")

        def run(kind: SegmentKind, *ids: str, **kwargs):
            event.start_segment(kind, **{"now": kwargs.get("now")} if kwargs.get("now") else {})
            for cid in ids:
                event.complete_item(kind, cid, **({"now": kwargs.get("now")} if kwargs.get("now") else {}))
            event.close_segment(kind, **{"now": kwargs.get("now")} if kwargs.get("now") else {})

        run(SegmentKind.PARADE, "c1", "c2", "c3")
        run(SegmentKind.MEDIA_ROLL, "c1", "c2", "c3")
        run(SegmentKind.ENTRANCE, "c1", "c2", "c3")
        run(SegmentKind.CEREMONY, "c1", "c2", "c3")
        run(SegmentKind.CERTIFICATE, "c1", "c2", "c3")
        event.start_segment(SegmentKind.MARKET)
        event.complete_item(SegmentKind.MARKET, "MK-01-01")
        event.close_segment(SegmentKind.MARKET)

        # 婚书签署后双方授权撤回不影响已交付记录。
        event.revoke_consent("p1a", Scope.PUBLIC_DISPLAY, reason="活动后要求撤下")
        cert = event.couples["c1"].cert_versions[0]
        self.assertTrue(cert.signed)
        self.assertEqual(cert.names["p1a"], "展示1A")

        view = event.director_view()
        self.assertEqual(view["total_couples"], 3)
        for key, value in view["progress"].items():
            expected = 1 if key == "market" else 3
            self.assertEqual(value["done"], expected, key)

    def test_revoke_public_display_propagates_before_media(self):
        event = build_event(couples=2)
        event.revoke_consent("p2b", Scope.PUBLIC_DISPLAY, reason="不愿公开")

        event.start_segment(SegmentKind.PARADE)
        marshal = event.staff_view(Station.PARADE_MARSHAL)
        c2 = next(q for q in marshal["queue"] if q["couple_no"] == 2)
        self.assertTrue(c2["camera_avoid"])
        event.complete_item(SegmentKind.PARADE, "c1")
        # c2 仍参与巡游（不镜头呈现），但大屏必须跳过。
        event.complete_item(SegmentKind.PARADE, "c2")
        event.close_segment(SegmentKind.PARADE)

        event.start_segment(SegmentKind.MEDIA_ROLL)
        screen = event.staff_view(Station.SCREEN_OPERATOR)
        c2 = next(q for q in screen["queue"] if q["couple_no"] == 2)
        self.assertEqual(c2["action"], "suppress")
        with self.assertRaises(OrchestrationError):
            event.complete_item(SegmentKind.MEDIA_ROLL, "c2")
        event.skip_suppressed(SegmentKind.MEDIA_ROLL, "c2")
        event.complete_item(SegmentKind.MEDIA_ROLL, "c1")
        event.close_segment(SegmentKind.MEDIA_ROLL)

        record = next(r for r in event.records
                      if r.segment_id == "seg-media" and r.couple_id == "c2")
        self.assertEqual(record.outcome, "skipped")

    def test_replace_media_before_and_after_play(self):
        event = build_event(couples=1)
        event.replace_media("c1", "asset-1-v2", note="更换精修版")

        event.start_segment(SegmentKind.MEDIA_ROLL)
        screen = event.staff_view(Station.SCREEN_OPERATOR)
        self.assertEqual(screen["queue"][0]["version"], 2)
        event.complete_item(SegmentKind.MEDIA_ROLL, "c1")
        event.close_segment(SegmentKind.MEDIA_ROLL)

        # 已播放后再替换：记录锁存 v2，后续环节看到 v3，且不能重复提交 v1。
        event.replace_media("c1", "asset-1-v3")
        record = next(r for r in event.records if r.segment_id == "seg-media")
        self.assertEqual(record.snapshot["media"]["version"], 2)
        with self.assertRaises(OrchestrationError):
            event.replace_media("c1", "asset-1-v2")

    def test_late_arrival_moves_to_tail_and_marks_gap(self):
        event = build_event(couples=3)
        event.start_segment(SegmentKind.PARADE)
        event.complete_item(SegmentKind.PARADE, "c1")
        move = event.report_late("c2")
        self.assertEqual((move["from_position"], move["to_position"]), (2, 4))
        marshal = event.staff_view(Station.PARADE_MARSHAL)
        self.assertIn(2, marshal["vacant_positions"])
        self.assertEqual([q["couple_no"] for q in marshal["queue"]], [3, 2])
        event.complete_item(SegmentKind.PARADE, "c3")
        event.complete_item(SegmentKind.PARADE, "c2")
        event.close_segment(SegmentKind.PARADE)

        # 入场顺序同步为 1、3、2。
        event.start_segment(SegmentKind.ENTRANCE)
        usher = event.staff_view(Station.USHER)
        self.assertEqual([q["couple_no"] for q in usher["queue"]], [1, 3, 2])
        event.close_segment(SegmentKind.ENTRANCE)

    def test_cannot_skip_after_item_completed(self):
        event = build_event(couples=2)
        event.start_segment(SegmentKind.PARADE)
        event.complete_item(SegmentKind.PARADE, "c1")
        with self.assertRaises(OrchestrationError):
            event.report_late("c1")
        event.close_segment(SegmentKind.PARADE)

    def test_cancel_couple_removes_from_pending_segments_but_keeps_records(self):
        event = build_event(couples=2)
        event.start_segment(SegmentKind.PARADE)
        event.complete_item(SegmentKind.PARADE, "c1")
        event.complete_item(SegmentKind.PARADE, "c2")
        event.close_segment(SegmentKind.PARADE)
        event.cancel_couple("c2")

        event.start_segment(SegmentKind.MEDIA_ROLL)
        screen = event.staff_view(Station.SCREEN_OPERATOR)
        self.assertEqual([q["couple_no"] for q in screen["queue"]], [1])
        event.close_segment(SegmentKind.MEDIA_ROLL)

        kept = [r for r in event.records if r.couple_id == "c2"]
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].outcome, "done")
        self.assertTrue(all(b.state != "booked" for b in event.couples["c2"].bookings) or
                        event.couples["c2"].bookings == [])

    def test_cancel_after_cert_signing_retains_certificate(self):
        event = build_event(couples=1)
        approve_and_print(event, "c1")
        event.start_segment(SegmentKind.CERTIFICATE)
        event.complete_item(SegmentKind.CERTIFICATE, "c1")
        event.close_segment(SegmentKind.CERTIFICATE)
        event.cancel_couple("c1")
        cert = event.couples["c1"].cert_versions[0]
        self.assertTrue(cert.signed and cert.delivered)
        log = [a.action for a in event.audit if a.target == "c1" and a.action == "cancel_couple"]
        self.assertEqual(log, ["cancel_couple"])

    def test_device_fault_fails_over_or_defers_unplayed_items(self):
        event = build_event(couples=2)
        event.start_segment(SegmentKind.MEDIA_ROLL)
        event.complete_item(SegmentKind.MEDIA_ROLL, "c1", device_id="screen-main")
        event.mark_device_fault("screen-main", reason="大屏无信号")
        # 自动改道备用屏：c2 仍可播放（device_id 指向备用设备）。
        screen = event.staff_view(Station.SCREEN_OPERATOR)
        c2 = next(q for q in screen["queue"] if q["couple_no"] == 2)
        self.assertEqual(c2["device_id"], "screen-backup")
        event.complete_item(SegmentKind.MEDIA_ROLL, "c2")
        event.close_segment(SegmentKind.MEDIA_ROLL)

        # 备用设备也故障时，未播项顺延到下一个有屏环节（婚书环节）。
        event2 = build_event(couples=1)
        event2.mark_device_fault("screen-main")
        event2.mark_device_fault("screen-backup", reason="全场无可用屏")
        event2.start_segment(SegmentKind.MEDIA_ROLL)
        view = event2.staff_view(Station.SCREEN_OPERATOR)
        self.assertEqual(view["queue"][0]["action"], "defer")
        event2.defer_item(SegmentKind.MEDIA_ROLL, "c1", reason="设备全部离线")
        event2.close_segment(SegmentKind.MEDIA_ROLL)

        approve_and_print(event2, "c1")
        event2.start_segment(SegmentKind.CERTIFICATE)
        cert_view = event2.staff_view(Station.SCREEN_OPERATOR)
        deferred = next(q for q in cert_view["queue"] if q["action"] == "play_deferred")
        self.assertIn("顺延", deferred["note"])
        event2.complete_item(SegmentKind.CERTIFICATE, "c1:deferred-media")
        # 顺延项播完后再签署电子婚书展示。
        self.assertFalse(any(q["action"] == "play_deferred"
                             for q in event2.staff_view(Station.SCREEN_OPERATOR)["queue"]))
        event2.complete_item(SegmentKind.CERTIFICATE, "c1")
        event2.close_segment(SegmentKind.CERTIFICATE)

    def test_name_revision_after_print_forces_new_version_and_blocks_signing(self):
        event = build_event(couples=1)
        approve_and_print(event, "c1")
        consistent, note = event.certificate_consistency(event.couples["c1"])
        self.assertTrue(consistent, note)

        event.set_display_name("p1a", "新展示名A")
        consistent, note = event.certificate_consistency(event.couples["c1"])
        self.assertFalse(consistent)
        event.start_segment(SegmentKind.CERTIFICATE)
        steward = event.staff_view(Station.CERT_STEWARD)
        c1 = next(q for q in steward["queue"] if q["couple_no"] == 1)
        self.assertEqual(c1["action"], "block")
        with self.assertRaises(OrchestrationError):
            event.complete_item(SegmentKind.CERTIFICATE, "c1")

        # 旧版实体作废 → 电子重批 → 按新快照重印后才能签署。
        event.approve_certificate("c1")
        self.assertFalse(event.staff_view(Station.CERT_STEWARD)["queue"][0]["consistent"])
        event.print_certificate("c1")
        event.complete_item(SegmentKind.CERTIFICATE, "c1")
        event.close_segment(SegmentKind.CERTIFICATE)
        self.assertEqual(event.couples["c1"].cert_versions[0].physical, "void")
        self.assertEqual(event.couples["c1"].cert_versions[1].names["p1a"], "新展示名A")

    def test_name_revision_after_signing_keeps_signed_record(self):
        event = build_event(couples=1)
        approve_and_print(event, "c1")
        event.start_segment(SegmentKind.CERTIFICATE)
        event.complete_item(SegmentKind.CERTIFICATE, "c1")
        event.close_segment(SegmentKind.CERTIFICATE)
        event.set_display_name("p1a", "婚后新展示名")
        cert = event.couples["c1"].cert_versions[0]
        self.assertTrue(cert.signed)
        self.assertEqual(cert.names["p1a"], "展示1A")
        consistent, _ = event.certificate_consistency(event.couples["c1"])
        self.assertTrue(consistent)

    def test_staff_views_are_segment_scoped_and_sensitive_free(self):
        event = build_event(couples=1)
        # 非当前环节不提供资料。
        with self.assertRaises(OrchestrationError):
            event.staff_view(Station.USHER)
        event.start_segment(SegmentKind.PARADE)
        view = event.staff_view(Station.PARADE_MARSHAL)
        blob = repr(view)
        for sensitive in ("legal_name", "id_doc_token", "token-p1a", "attire_size", "法定"):
            self.assertNotIn(sensitive, blob)
        event.close_segment(SegmentKind.PARADE)
        with self.assertRaises(OrchestrationError):
            event.staff_view(Station.PARADE_MARSHAL)

    def test_person_preview_shows_upcoming_and_changes(self):
        event = build_event(couples=1)
        event.add_market_service("纪念照", "14:00", 5)
        event.book_market("c1", "纪念照", "14:00")
        event.set_display_name("p1a", "展示1A-改")
        preview = event.person_preview("p1a")
        self.assertEqual(preview["display_name"], "展示1A-改")
        kinds = {u["kind"] for u in preview["upcoming"]}
        self.assertEqual(kinds, {k.value for k in SegmentKind})
        actions = {c["action"] for c in preview["changes"]}
        self.assertIn("revise_display_name", actions)
        self.assertTrue(preview["consents"]["public_display"]["active"])

    def test_market_booking_requires_consent_and_tracks_delivery(self):
        event = build_event(couples=2)
        event.add_market_service("手作摊位", "15:00", 1)
        event.book_market("c1", "手作摊位", "15:00")
        with self.assertRaises(OrchestrationError):
            event.book_market("c2", "手作摊位", "15:00")
        event.revoke_consent("p2a", Scope.MARKET)
        with self.assertRaises(OrchestrationError):
            event.book_market("c2", "手作摊位", "16:00")
        event.add_market_service("手作摊位", "16:00", 1)
        # 撤回后重新授权可预约；取消参加时未交付预约自动取消。
        event.grant_consent("p2a", Scope.MARKET)
        event.book_market("c2", "手作摊位", "16:00")
        event.cancel_couple("c2")
        self.assertEqual(event.couples["c2"].bookings[0].state, "cancelled")

    def test_director_checks_and_retention_deletion(self):
        event = build_event(couples=2)
        approve_and_print(event, "c1")  # c2 故意不发布婚书
        view = event.director_view()
        self.assertTrue(any("婚书" in issue for issue in view["issues"]))
        due_labels = [d["who"] for d in view["scheduled_deletions"]]
        self.assertIn("第01对-A", due_labels)

        # c1 的影像授权在活动后撤回；到期删除清除素材标识与证件资料，但婚书保留。
        event.revoke_consent("p1a", Scope.PUBLIC_DISPLAY)
        purge_day = EVENT_DAY + timedelta(days=31)
        purged = event.run_retention_deletions(purge_day)
        scopes = {(p["person_id"], p["scope"]) for p in purged}
        self.assertIn(("p1a", "public_display"), scopes)
        self.assertIn(("p1a", "identity_verification"), scopes)
        self.assertIsNone(event.persons["p1a"].legal_name)
        self.assertTrue(all(a.state == "purged" for a in event.couples["c1"].assets))
        cert = event.couples["c1"].cert_versions[0]
        self.assertEqual(cert.physical, "printed")
        self.assertFalse(cert.signed)  # 本例婚书尚未签署，仅已发布并付印
        # 已删除不再重复列入到期清单。
        remaining = event.deletions_due(purge_day)
        self.assertFalse(any(d["scope"] == "identity_verification" and d["who"] == "第01对-A"
                             for d in remaining))

    def test_certificate_consent_revoke_blocks_pending_signing(self):
        event = build_event(couples=1)
        approve_and_print(event, "c1")
        event.revoke_consent("p1b", Scope.CERTIFICATE, reason="临时改变主意")
        event.start_segment(SegmentKind.CERTIFICATE)
        steward = event.staff_view(Station.CERT_STEWARD)
        self.assertEqual(steward["queue"][0]["action"], "withhold")
        with self.assertRaises(OrchestrationError):
            event.complete_item(SegmentKind.CERTIFICATE, "c1")
        # 重新授权后：旧版已停用，须重批重印才能签署。
        event.grant_consent("p1b", Scope.CERTIFICATE)
        event.approve_certificate("c1")
        with self.assertRaises(OrchestrationError):
            event.complete_item(SegmentKind.CERTIFICATE, "c1")
        event.print_certificate("c1")
        event.complete_item(SegmentKind.CERTIFICATE, "c1")
        self.assertEqual(event.couples["c1"].cert_versions[0].physical, "void")
        self.assertTrue(event.couples["c1"].cert_versions[1].signed)

    def test_market_consent_revoke_cancels_pending_booking(self):
        event = build_event(couples=1)
        event.add_market_service("纪念照", "14:00", 2)
        event.book_market("c1", "纪念照", "14:00")
        event.revoke_consent("p1a", Scope.MARKET)
        self.assertEqual(event.couples["c1"].bookings[0].state, "cancelled")
        event.start_segment(SegmentKind.MARKET)
        self.assertEqual(event.staff_view(Station.MARKET_DESK)["queue"], [])
        event.close_segment(SegmentKind.MARKET)

    def test_certificate_scope_retention_is_permanent(self):
        event = build_event(couples=1)
        view = event.director_view()
        cert_deletions = [d for d in view["scheduled_deletions"] if d["scope"] == "certificate"]
        self.assertEqual(cert_deletions, [])


if __name__ == "__main__":
    unittest.main()
