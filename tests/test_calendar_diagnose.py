"""节假日表诊断 API 测试：逐日判定、补班/放假、周末规则、截止时刻与参数拒绝。"""
DIAG = "/api/v1/calendars/diagnose"


def _post(client, **overrides):
    payload = {
        "calendar_code": "CN",
        "timezone": "Asia/Shanghai",
        "start_at": "2025-10-01T10:00:00+08:00",
        "business_days": 10,
        "cutoff": "18:00",
    }
    payload.update(overrides)
    return client.post(DIAG, json=payload)


# ---------------------------------------------------------------- 正常诊断

def test_diagnose_golden_week_counts_to_oct21(client):
    # 锚点 2025-10-01（国庆假期第 1 天），从次日起 10 个工作日 => 2025-10-21
    r = _post(client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["nth_business_day"] == "2025-10-21"
    assert body["deadline_local"] == "2025-10-21T18:00:00+08:00"
    assert body["deadline_utc"] == "2025-10-21T10:00:00+00:00"
    assert body["business_days_counted"] == 10
    assert body["business_days_requested"] == 10
    assert body["count_from"] == "2025-10-02"
    assert body["read_only"] is True

    days = body["days"]
    # 每天都带齐：日期、工作/休息、声明、周末规则、累计数、跳过原因
    for d in days:
        assert set(("date", "weekday", "is_weekend", "kind", "declaration",
                    "rule_source", "weekend_default_rule", "cumulative_business_days",
                    "skip_reason_code", "skip_reason")).issubset(d)
    # 最后一天即第 10 个工作日
    assert days[-1]["date"] == "2025-10-21"
    assert days[-1]["kind"] == "work"
    assert days[-1]["cumulative_business_days"] == 10
    assert days[-1]["skip_reason"] is None
    # 累计工作日数单调不减、休息日不增长
    prev = 0
    for d in days:
        assert d["cumulative_business_days"] >= prev
        if d["kind"] == "rest":
            assert d["cumulative_business_days"] == prev
            assert d["skip_reason"]
        prev = d["cumulative_business_days"]


def test_diagnose_holiday_on_weekday_is_skipped(client):
    # 2025-10-06（周一）、10-07（周二）、10-08（周三）均为显式放假 -> 跳过
    body = _post(client).json()
    by_date = {d["date"]: d for d in body["days"]}
    for iso in ("2025-10-06", "2025-10-07", "2025-10-08"):
        d = by_date[iso]
        assert d["kind"] == "rest" and d["is_weekend"] is False
        assert d["declaration"] == "holiday"
        assert d["rule_source"] == "calendar_declared"
        assert d["skip_reason_code"] == "DECLARED_HOLIDAY_SKIPPED"
        assert "放假" in d["skip_reason"] and "跳过" in d["skip_reason"]


def test_diagnose_makeup_workday_on_weekend_counts(client):
    # 2025-10-11（周六）国庆调休上班 -> 计入；2025-10-12（周日）无声明 -> 按周末休息
    body = _post(client).json()
    by_date = {d["date"]: d for d in body["days"]}
    sat = by_date["2025-10-11"]
    assert sat["kind"] == "work" and sat["is_weekend"] is True
    assert sat["declaration"] == "workday" and sat["declaration_name"] == "国庆调休上班"
    assert sat["skip_reason"] is None

    sun = by_date["2025-10-12"]
    assert sun["kind"] == "rest" and sun["declaration"] is None
    assert sun["rule_source"] == "weekend_default"
    assert sun["skip_reason_code"] == "WEEKEND_DEFAULT_REST"


def test_diagnose_declared_holiday_on_weekend_marked(client):
    # 2025-10-04（周六）、10-05（周日）本身既是周末又被显式声明为放假
    body = _post(client).json()
    by_date = {d["date"]: d for d in body["days"]}
    for iso in ("2025-10-04", "2025-10-05"):
        d = by_date[iso]
        assert d["kind"] == "rest" and d["is_weekend"] is True
        assert d["declaration"] == "holiday"
        assert d["skip_reason_code"] == "DECLARED_HOLIDAY_ON_WEEKEND"


def test_diagnose_spring_festival_sunday_makeup(client):
    # 锚点 2025-01-23（周四），2 个工作日：1/24(五)=1，1/25(六)休，1/26(日·补班)=2
    r = _post(client, start_at="2025-01-23T09:00:00+08:00", business_days=2)
    body = r.json()
    assert body["nth_business_day"] == "2025-01-26"
    assert body["deadline_local"] == "2025-01-26T18:00:00+08:00"
    assert body["deadline_utc"] == "2025-01-26T10:00:00+00:00"
    sunday = next(d for d in body["days"] if d["date"] == "2025-01-26")
    assert sunday["kind"] == "work" and sunday["is_weekend"] is True
    assert sunday["declaration"] == "workday"
    assert sunday["cumulative_business_days"] == 2


def test_diagnose_default_weekend_rule_in_plain_week(client):
    # 2025-03-06（周四）起 3 个工作日：3/7(五)=1，3/8(六)休，3/9(日)休，3/10(一)=2，3/11(二)=3
    r = _post(client, start_at="2025-03-06T09:00:00+08:00", business_days=3,
              cutoff="12:30:00")
    body = r.json()
    assert body["nth_business_day"] == "2025-03-11"
    assert body["deadline_local"] == "2025-03-11T12:30:00+08:00"
    assert body["deadline_utc"] == "2025-03-11T04:30:00+00:00"
    by_date = {d["date"]: d for d in body["days"]}
    assert by_date["2025-03-08"]["kind"] == "rest"
    assert by_date["2025-03-08"]["rule_source"] == "weekend_default"
    assert by_date["2025-03-10"]["kind"] == "work"
    assert body["rest_days_skipped"] == 2
    assert body["days_enumerated"] == 5


def test_diagnose_start_date_excluded_count_from_next_day(client):
    # 起始日本身绝不计入；2025-03-07 为周五，从 3/8 起算，1 个工作日 => 3/10（周一）
    r = _post(client, start_at="2025-03-07T23:59:00+08:00", business_days=1)
    body = r.json()
    assert body["start_date_local"] == "2025-03-07"
    assert body["count_from"] == "2025-03-08"
    assert body["nth_business_day"] == "2025-03-10"
    assert body["days"][0]["date"] == "2025-03-08"


def test_diagnose_timezone_offset_drives_local_date(client):
    # 同一物理时刻：+08:00 已是 3/8（周六），UTC 仍是 3/7；按提交时区取当地日
    r_utc = _post(client, timezone="UTC",
                  start_at="2025-03-07T20:00:00+00:00", business_days=1)
    r_sh = _post(client, timezone="Asia/Shanghai",
                 start_at="2025-03-07T20:00:00+00:00", business_days=1)
    assert r_utc.json()["start_date_local"] == "2025-03-07"
    assert r_utc.json()["nth_business_day"] == "2025-03-10"
    assert r_sh.json()["start_date_local"] == "2025-03-08"
    assert r_sh.json()["nth_business_day"] == "2025-03-10"


def test_diagnose_dst_spring_forward_uses_post_transition_offset(client):
    # America/New_York 2025-03-09 春令时；截止日 3/11 在 DST 后，UTC 偏移 -4
    r = _post(client, timezone="America/New_York",
              start_at="2025-03-06T12:00:00-05:00", business_days=3, cutoff="18:00")
    body = r.json()
    assert body["nth_business_day"] == "2025-03-11"
    assert body["deadline_local"] == "2025-03-11T18:00:00-04:00"
    assert body["deadline_utc"] == "2025-03-11T22:00:00+00:00"


def test_diagnose_cutoff_accepts_hhmm_and_hhmmss(client):
    a = _post(client, start_at="2025-03-06T09:00:00+08:00", business_days=1,
              cutoff="09:00").json()
    b = _post(client, start_at="2025-03-06T09:00:00+08:00", business_days=1,
              cutoff="09:00:00").json()
    assert a["deadline_local"] == b["deadline_local"] == "2025-03-07T09:00:00+08:00"
    assert a["cutoff_local"] == "09:00:00"


def test_diagnose_sixty_business_days_accepted(client):
    r = _post(client, start_at="2025-01-02T09:00:00+08:00", business_days=60)
    assert r.status_code == 200
    assert r.json()["business_days_counted"] == 60


# ---------------------------------------------------------------- 参数拒绝

def test_diagnose_naive_start_at_rejected(client):
    r = _post(client, start_at="2025-10-01T10:00:00")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "INVALID_REQUEST"


def test_diagnose_missing_fields_rejected(client):
    r = client.post(DIAG, json={"calendar_code": "CN"})
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "INVALID_REQUEST"
    fields = {d["field"] for d in err["details"]}
    assert {"timezone", "start_at", "business_days", "cutoff"} <= fields


def test_diagnose_business_days_out_of_range_rejected(client):
    for bad in (0, 61, -3):
        r = _post(client, business_days=bad)
        assert r.status_code == 422, r.text
        assert r.json()["error"]["code"] == "INVALID_REQUEST"


def test_diagnose_business_days_wrong_type_rejected(client):
    r = _post(client, business_days="ten")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "INVALID_REQUEST"


def test_diagnose_bad_cutoff_rejected(client):
    for bad in ("24:00", "18:60", "abc", "1800", "12:30:60"):
        r = _post(client, cutoff=bad)
        assert r.status_code == 422, (bad, r.text)
        assert r.json()["error"]["code"] == "INVALID_REQUEST"


def test_diagnose_unknown_timezone_rejected(client):
    r = _post(client, timezone="Asia/NoSuchCity")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "TIMEZONE_INVALID"


def test_diagnose_unknown_calendar_rejected(client):
    r = _post(client, calendar_code="ZZ")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "CALENDAR_NOT_FOUND"


def test_diagnose_start_at_bad_iso_rejected(client):
    r = _post(client, start_at="not-a-time")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "INVALID_REQUEST"


# ---------------------------------------------------------------- 只读保证

def test_diagnose_is_read_only(client):
    """诊断不得修改日历、政策、订单或凭证：重复调用结果稳定，日历表行数不变。"""
    from app import db
    before = db.q1("SELECT COUNT(*) AS c FROM calendars WHERE calendar_code = 'CN'")["c"]
    r1 = _post(client)
    r2 = _post(client)
    after = db.q1("SELECT COUNT(*) AS c FROM calendars WHERE calendar_code = 'CN'")["c"]
    assert r1.json()["days"] == r2.json()["days"]
    assert before == after
    # 没有订单、事件、凭证被创建
    assert db.q1("SELECT COUNT(*) AS c FROM orders")["c"] == 0
    assert db.q1("SELECT COUNT(*) AS c FROM events")["c"] == 0
    assert db.q1("SELECT COUNT(*) AS c FROM claim_vouchers")["c"] == 0
    assert db.q1("SELECT COUNT(*) AS c FROM policies")["c"] == 2
