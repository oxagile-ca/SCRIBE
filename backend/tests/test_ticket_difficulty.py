import ticket_difficulty as td


def test_empty_description_is_easy():
    assert td.compute_difficulty("") == ("Easy", 0)
    assert td.compute_difficulty(None) == ("Easy", 0)


def test_two_acs_is_easy():
    desc = "Acceptance Criteria\n- the first criterion line\n- the second criterion line"
    assert td.count_acceptance_criteria(desc) == 2
    assert td.compute_difficulty(desc) == ("Easy", 2)


def test_four_acs_is_medium():
    desc = "AC:\n- criterion alpha line\n- criterion beta line\n- criterion gamma line\n- criterion delta line"
    assert td.count_acceptance_criteria(desc) == 4
    assert td.compute_difficulty(desc)[0] == "Medium"


def test_six_acs_is_hard():
    desc = "Acceptance Criteria\n" + "\n".join(f"- criterion number {i} here" for i in range(6))
    assert td.count_acceptance_criteria(desc) == 6
    assert td.compute_difficulty(desc)[0] == "Hard"


def test_long_description_bumps_score_over_bucket():
    # 5 ACs (Medium) + a long body (>1200 chars) bumps +1 → 6 → Hard
    body = "x" * 1300
    desc = "Acceptance Criteria\n" + "\n".join(f"- criterion number {i} here" for i in range(5)) + "\n" + body
    label, score = td.compute_difficulty(desc)
    assert score >= 6 and label == "Hard"


def test_short_bullets_under_threshold_not_counted():
    # bullets <= 10 chars after de-bulleting are ignored (mirrors frontend extractACs)
    desc = "- short\n- ok"
    assert td.count_acceptance_criteria(desc) == 0
    assert td.compute_difficulty(desc) == ("Easy", 0)
