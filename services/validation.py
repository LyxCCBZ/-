import math

from .rules import option_key


class ValidationError(Exception):
    def __init__(self, message, field_errors=None):
        super().__init__(message)
        self.message = message
        self.field_errors = field_errors or {}


def _bool(value):
    return str(value).lower() in {"1", "true", "on", "yes"}


def _int_or_none(value):
    value = (value or "").strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise ValidationError("数量或长度限制必须为整数") from exc


def _float_or_none(value):
    value = (value or "").strip()
    if not value:
        return None
    try:
        number = float(value)
    except ValueError as exc:
        raise ValidationError("数字范围必须是合法数字") from exc
    if not math.isfinite(number):
        raise ValidationError("数字范围必须是有限数字")
    return number


def build_question_document(form):
    title = (form.get("title") or "").strip()
    if not title:
        raise ValidationError("题目内容不能为空")
    if len(title) > 300:
        raise ValidationError("题目内容不能超过 300 字")

    question_type = form.get("type")
    if question_type not in {"single", "multiple", "text", "number"}:
        raise ValidationError("请选择有效的题型")

    required = _bool(form.get("required"))
    options = []
    validation = {}

    if question_type in {"single", "multiple"}:
        raw_options = [line.strip() for line in (form.get("options") or "").splitlines() if line.strip()]
        if len(raw_options) < 2:
            raise ValidationError("选择题至少需要两个选项")
        if len(raw_options) > 50:
            raise ValidationError("选择题最多支持 50 个选项")
        if len(set(raw_options)) != len(raw_options):
            raise ValidationError("选项内容不能重复")
        options = [{"key": option_key(index), "text": text} for index, text in enumerate(raw_options)]

        if question_type == "multiple":
            min_select = _int_or_none(form.get("min_select")) or 0
            max_select = _int_or_none(form.get("max_select"))
            if max_select is None:
                max_select = len(options)
            if min_select < 0 or max_select < 1:
                raise ValidationError("多选题选择数量限制必须为正整数")
            if min_select > max_select:
                raise ValidationError("多选题的最少选择数量不能大于最多选择数量")
            if max_select > len(options):
                raise ValidationError("多选题的最多选择数量不能超过选项数")
            if required and min_select == 0:
                min_select = 1
            validation = {"min_select": min_select, "max_select": max_select}

    elif question_type == "text":
        min_length = _int_or_none(form.get("min_length")) or 0
        max_length = _int_or_none(form.get("max_length"))
        if min_length < 0 or (max_length is not None and max_length < 1):
            raise ValidationError("文本长度限制必须为正整数")
        if max_length is not None and min_length > max_length:
            raise ValidationError("文本最少字数不能大于最多字数")
        validation = {"min_length": min_length, "max_length": max_length}

    elif question_type == "number":
        minimum = _float_or_none(form.get("minimum"))
        maximum = _float_or_none(form.get("maximum"))
        integer_only = _bool(form.get("integer_only"))
        if minimum is not None and maximum is not None and minimum > maximum:
            raise ValidationError("数字最小值不能大于最大值")
        validation = {"minimum": minimum, "maximum": maximum, "integer_only": integer_only}

    return {
        "type": question_type,
        "title": title,
        "required": required,
        "options": options,
        "validation": validation,
        "jumps": [],
    }


def validate_answer(question, raw_value):
    question_type = question["type"]
    required = question.get("required", False)

    if question_type == "multiple":
        values = raw_value if isinstance(raw_value, list) else ([raw_value] if raw_value else [])
        values = list(dict.fromkeys(str(item) for item in values if item))
        if required and not values:
            raise ValidationError("该题为必答题，请至少选择一项")
        allowed = {option["key"] for option in question.get("options", [])}
        if any(item not in allowed for item in values):
            raise ValidationError("提交的选项不存在，请重新填写")
        rules = question.get("validation") or {}
        min_select = rules.get("min_select", 0)
        max_select = rules.get("max_select", len(allowed) or 0)
        if values and len(values) < min_select:
            raise ValidationError(f"该题最少需要选择 {min_select} 项")
        if len(values) > max_select:
            raise ValidationError(f"该题最多只能选择 {max_select} 项")
        return values

    value = raw_value if isinstance(raw_value, str) else ""
    value = value.strip()

    if question_type == "single":
        if not value:
            if required:
                raise ValidationError("该题为必答题，请选择一个选项")
            return None
        allowed = {option["key"] for option in question.get("options", [])}
        if value not in allowed:
            raise ValidationError("提交的选项不存在，请重新填写")
        return value

    if question_type == "text":
        if not value:
            if required:
                raise ValidationError("该题为必答题，请填写内容")
            return None
        rules = question.get("validation") or {}
        min_length = rules.get("min_length", 0)
        max_length = rules.get("max_length")
        if len(value) < min_length:
            raise ValidationError(f"该题至少需要填写 {min_length} 个字")
        if max_length is not None and len(value) > max_length:
            raise ValidationError(f"该题最多只能填写 {max_length} 个字")
        return value

    if question_type == "number":
        if not value:
            if required:
                raise ValidationError("该题为必答题，请填写数字")
            return None
        try:
            number = float(value)
        except ValueError as exc:
            raise ValidationError("请输入合法数字") from exc
        if not math.isfinite(number):
            raise ValidationError("请输入有限数字")
        rules = question.get("validation") or {}
        if rules.get("integer_only") and not number.is_integer():
            raise ValidationError("该题只允许填写整数")
        minimum = rules.get("minimum")
        maximum = rules.get("maximum")
        if minimum is not None and number < minimum:
            raise ValidationError(f"数字不能小于 {minimum:g}")
        if maximum is not None and number > maximum:
            raise ValidationError(f"数字不能大于 {maximum:g}")
        return int(number) if number.is_integer() else number

    raise ValidationError("不支持的题型")


def parse_jump_condition(question, operator, raw_value):
    question_type = question["type"]
    raw_value = (raw_value or "").strip()
    if not raw_value:
        raise ValidationError("请填写跳转条件")

    if question_type == "single":
        if operator not in {"eq", "neq"}:
            raise ValidationError("单选题仅支持“等于/不等于”跳转")
        allowed = {option["key"] for option in question["options"]}
        if raw_value not in allowed:
            raise ValidationError("跳转条件中的选项不存在")
        return {"operator": operator, "value": raw_value}

    if question_type == "multiple":
        if operator not in {"includes_any", "includes_all", "not_includes"}:
            raise ValidationError("多选题仅支持“包含任意/包含全部/不包含”跳转")
        values = [item.strip().upper() for item in raw_value.replace("，", ",").split(",") if item.strip()]
        allowed = {option["key"] for option in question["options"]}
        if not values or any(item not in allowed for item in values):
            raise ValidationError("跳转条件包含无效选项")
        return {"operator": operator, "value": values}

    if question_type == "number":
        if operator not in {"eq", "neq", "gt", "gte", "lt", "lte", "between"}:
            raise ValidationError("数字题仅支持数值比较跳转")
        try:
            if operator == "between":
                parts = [item.strip() for item in raw_value.replace("，", ",").split(",") if item.strip()]
                if len(parts) != 2:
                    raise ValueError
                value = [float(parts[0]), float(parts[1])]
                if not all(math.isfinite(item) for item in value) or value[0] > value[1]:
                    raise ValueError
            else:
                value = float(raw_value)
                if not math.isfinite(value):
                    raise ValueError
        except ValueError as exc:
            raise ValidationError("数字跳转条件格式不正确") from exc
        return {"operator": operator, "value": value}

    if question_type == "text":
        if operator not in {"eq", "neq", "contains"}:
            raise ValidationError("文本题仅支持“等于/不等于/包含”跳转")
        return {"operator": operator, "value": raw_value}

    raise ValidationError("该题型不支持跳转")

