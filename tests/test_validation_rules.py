from bson import ObjectId

from services.rules import describe_condition, next_question, option_key
from services.validation import ValidationError, parse_jump_condition, validate_answer


def question(**overrides):
    base = {
        "_id": ObjectId(),
        "type": "single",
        "title": "测试题",
        "required": True,
        "options": [
            {"key": "A", "text": "选项 A"},
            {"key": "B", "text": "选项 B"},
            {"key": "C", "text": "选项 C"},
        ],
        "validation": {},
        "jumps": [],
    }
    base.update(overrides)
    return base


def test_option_keys_extend_beyond_z():
    assert option_key(0) == "A"
    assert option_key(25) == "Z"
    assert option_key(26) == "AA"


def test_single_required_and_option_validation():
    item = question()
    try:
        validate_answer(item, "")
    except ValidationError as exc:
        assert "必答" in exc.message
    else:
        raise AssertionError("empty required answer should fail")
    assert validate_answer(item, "A") == "A"
    try:
        validate_answer(item, "Z")
    except ValidationError as exc:
        assert "不存在" in exc.message
    else:
        raise AssertionError("unknown option should fail")


def test_multiple_min_max_and_deduplication():
    item = question(
        type="multiple",
        validation={"min_select": 2, "max_select": 3},
    )
    assert validate_answer(item, ["A", "A", "B"]) == ["A", "B"]
    try:
        validate_answer(item, ["A"])
    except ValidationError as exc:
        assert "最少" in exc.message
    else:
        raise AssertionError("too few selections should fail")
    assert validate_answer(item, ["A", "B", "C"]) == ["A", "B", "C"]


def test_text_length_and_number_range_validation():
    text_item = question(type="text", validation={"min_length": 3, "max_length": 5})
    assert validate_answer(text_item, " 你好啊 ") == "你好啊"
    try:
        validate_answer(text_item, "好")
    except ValidationError as exc:
        assert "至少" in exc.message
    else:
        raise AssertionError("short text should fail")

    number_item = question(
        type="number", validation={"minimum": 0, "maximum": 120, "integer_only": True}
    )
    assert validate_answer(number_item, "18") == 18
    try:
        validate_answer(number_item, "18.5")
    except ValidationError as exc:
        assert "整数" in exc.message
    else:
        raise AssertionError("non-integer should fail")
    try:
        validate_answer(number_item, "121")
    except ValidationError as exc:
        assert "不能大于" in exc.message
    else:
        raise AssertionError("out of range should fail")


def test_jump_condition_parsing_and_description():
    single = question()
    condition = parse_jump_condition(single, "eq", "B")
    assert condition == {"operator": "eq", "value": "B"}
    assert "选项 B" in describe_condition(condition, single)

    multiple = question(type="multiple", validation={"min_select": 0, "max_select": 3})
    condition = parse_jump_condition(multiple, "includes_any", "A, C")
    assert condition["value"] == ["A", "C"]

    number = question(
        type="number", validation={"minimum": 0, "maximum": 120, "integer_only": True}
    )
    condition = parse_jump_condition(number, "between", "18,60")
    assert condition["value"] == [18.0, 60.0]
    assert "介于" in describe_condition(condition, number)


def test_next_question_first_match_then_default_then_end():
    q1, q2, q3 = question(), question(), question()
    q1["jumps"] = [
        {"condition": {"operator": "eq", "value": "A"}, "target_question_id": q3["_id"]},
        {"condition": {"operator": "eq", "value": "B"}, "target_question_id": "END"},
    ]
    questions = [q1, q2, q3]
    assert next_question(q1, "A", questions)["_id"] == q3["_id"]
    assert next_question(q1, "B", questions) is None
    assert next_question(q1, "C", questions)["_id"] == q2["_id"]
    assert next_question(q3, "A", questions) is None

