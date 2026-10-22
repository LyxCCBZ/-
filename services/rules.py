def option_key(index):
    key = ""
    number = index + 1
    while number:
        number, remainder = divmod(number - 1, 26)
        key = chr(65 + remainder) + key
    return key


def ordered_questions(db, survey_id):
    return list(db.questions.find({"survey_id": survey_id}).sort("order", 1))


def question_map(questions):
    return {str(question["_id"]): question for question in questions}


def describe_condition(condition, question):
    operator = condition.get("operator")
    value = condition.get("value")
    options = {option["key"]: option["text"] for option in question.get("options", [])}

    if operator == "eq":
        return f"等于 {options.get(value, value)}"
    if operator == "neq":
        return f"不等于 {options.get(value, value)}"
    if operator == "contains":
        return f"包含“{value}”"
    if operator == "gt":
        return f"大于 {value:g}"
    if operator == "gte":
        return f"大于等于 {value:g}"
    if operator == "lt":
        return f"小于 {value:g}"
    if operator == "lte":
        return f"小于等于 {value:g}"
    if operator == "between":
        return f"介于 {value[0]:g} 与 {value[1]:g} 之间"
    if operator == "includes_any":
        return "包含任意一项：" + ", ".join(options.get(item, item) for item in value)
    if operator == "includes_all":
        return "同时包含：" + ", ".join(options.get(item, item) for item in value)
    if operator == "not_includes":
        return "不包含：" + ", ".join(options.get(item, item) for item in value)
    return "满足条件"


def _matches(condition, answer):
    operator = condition.get("operator")
    expected = condition.get("value")

    if answer is None:
        return False

    if operator == "eq":
        return answer == expected
    if operator == "neq":
        return answer != expected
    if operator == "gt":
        return float(answer) > float(expected)
    if operator == "gte":
        return float(answer) >= float(expected)
    if operator == "lt":
        return float(answer) < float(expected)
    if operator == "lte":
        return float(answer) <= float(expected)
    if operator == "between":
        return float(expected[0]) <= float(answer) <= float(expected[1])
    if operator == "contains":
        return str(expected) in str(answer)
    if operator == "includes_any":
        return bool(set(answer or []) & set(expected or []))
    if operator == "includes_all":
        return set(expected or []).issubset(set(answer or []))
    if operator == "not_includes":
        return not bool(set(answer or []) & set(expected or []))
    return False


def next_question(question, answer, questions):
    for rule in question.get("jumps", []):
        if _matches(rule.get("condition", {}), answer):
            target = rule.get("target_question_id")
            if target == "END":
                return None
            for candidate in questions:
                if str(candidate["_id"]) == str(target):
                    return candidate
            return None

    for index, candidate in enumerate(questions):
        if candidate["_id"] == question["_id"]:
            return questions[index + 1] if index + 1 < len(questions) else None
    return None

