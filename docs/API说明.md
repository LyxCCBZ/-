# API 说明

## 1. 接口风格

第一阶段以服务端渲染页面为主，接口分为三类：

1. 表单接口：提交后 302 重定向到页面。
2. 页面接口：返回 HTML。
3. JSON 接口：健康检查与统计查询。

所有接口默认使用同源 Cookie 会话。问卷管理接口要求登录且当前用户是问卷所有者。

## 2. 账号接口

### GET /register

返回注册页面。

### POST /register

表单字段：

| 字段 | 必填 | 规则 |
| --- | --- | --- |
| username | 是 | 3-30 位中文、字母、数字、下划线 |
| password | 是 | 至少 6 位 |
| confirm_password | 是 | 与 password 一致 |

成功：302 到 /login，并提示注册成功。  
失败：返回注册页并提示具体原因，如用户名重复、两次密码不一致。

### GET /login

返回登录页面。可带查询参数 next，用于登录后返回原页面；只接受站内相对路径。

### POST /login

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| username | 是 | 用户名 |
| password | 是 | 密码 |
| next | 否 | 站内相对路径 |

成功：302 到 next 或 /dashboard。  
失败：返回登录页并提示用户名或密码错误。

### POST /logout

清空会话并 302 到 /login。

## 3. 问卷管理接口

以下接口均要求登录。

### GET /dashboard

返回当前用户创建的问卷列表，按创建时间倒序；附带每个问卷的已提交答卷数。

### GET /surveys/new

返回新建问卷页面。

### POST /surveys/new

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| title | 是 | 1-200 字 |
| description | 否 | 最多 2000 字 |
| allow_anonymous | 否 | 出现即 true |
| allow_multiple | 否 | 出现即 true |

成功：302 到 /surveys/新问卷ID。  
新问卷初始状态为 draft，deadline 为 null。

### GET /surveys/ID

返回问卷管理页面，包含设置、题目列表、跳转规则、公开链接和提交概览。

权限：owner_id 必须等于当前用户，否则 404。

### POST /surveys/ID/settings

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| title | 是 | 1-200 字 |
| description | 否 | 最多 2000 字 |
| deadline | 否 | datetime-local 格式，必须晚于当前时间 |
| allow_anonymous | 否 | 仅 draft 状态可修改 |
| allow_multiple | 否 | 仅 draft 状态可修改 |

已关闭问卷不可修改。

### POST /surveys/ID/publish

将 draft 状态改为 published。

前置条件：

- 至少有一道题。
- 截止时间未过。
- 当前用户是所有者。
- 当前状态必须是 draft。

成功：302 回管理页，生成可使用公开链接。  
失败：回管理页并提示原因。

### POST /surveys/ID/close

将 published 状态改为 closed，并记录 closed_at。关闭后新的填写会被阻止。

### POST /surveys/ID/questions

仅在 draft 状态可用。表单字段：

| 字段 | 适用题型 | 说明 |
| --- | --- | --- |
| type | 全部 | single / multiple / text / number |
| title | 全部 | 题目内容 |
| required | 全部 | 出现即必答 |
| options | single / multiple | 每行一个选项，至少两个 |
| min_select | multiple | 最少选择数，默认 0 |
| max_select | multiple | 最多选择数，留空为选项总数 |
| min_length | text | 最少字数，默认 0 |
| max_length | text | 最多字数，留空为不限 |
| minimum | number | 最小值，可空 |
| maximum | number | 最大值，可空 |
| integer_only | number | 出现即只允许整数 |

成功：302 回管理页，order 自动递增。  
失败：提示题型、选项、数量或范围错误。

### POST /surveys/ID/questions/QID/delete

仅在 draft 状态可用。删除题目后：

- 自动重排剩余题目 order。
- 自动删除所有指向该题目的跳转规则。

### POST /surveys/ID/questions/QID/jumps

仅在 draft 状态可用。表单字段：

| 字段 | 说明 |
| --- | --- |
| operator | eq、neq、includes_any、includes_all、not_includes、gt、gte、lt、lte、between、contains |
| value | 条件值；多个选项用逗号分隔；between 写 min,max |
| target | 目标题目 ID 或 END |

前置条件：目标必须是当前题目之后的题目，或 END；不能跳回前面，防止循环。

### POST /surveys/ID/questions/QID/jumps/INDEX/delete

删除指定位置的跳转规则。INDEX 从 0 开始。

### GET /surveys/ID/stats

返回整卷统计页面。

查询参数：

| 参数 | 说明 |
| --- | --- |
| question | 可选，题目 ObjectId；提供时只显示该题 |

页面展示总提交数、各题回答人数、选项计数、文本内容、数字均值/最小值/最大值。

### GET /api/surveys/ID/stats

要求登录且为问卷所有者。返回 JSON。

响应示例：

    {
      "survey_id": "66f0...",
      "response_count": 2,
      "questions": [
        {
          "question": {
            "_id": "66f1...",
            "order": 1,
            "type": "single",
            "title": "你更常用的学习方式是？",
            "options": [
              { "key": "A", "text": "独自学习" },
              { "key": "B", "text": "小组讨论" }
            ]
          },
          "answered_count": 2,
          "answer_values": ["A", "B"],
          "option_counts": [
            { "key": "A", "text": "独自学习", "count": 1, "percent": 50.0 },
            { "key": "B", "text": "小组讨论", "count": 1, "percent": 50.0 }
          ],
          "average": null,
          "minimum": null,
          "maximum": null
        }
      ]
    }

数字题的 average/minimum/maximum 为数字；其他题为 null。

## 4. 公开填写接口

### GET /survey/公开编号

要求登录。返回问卷说明、题目数量、截止时间、匿名设置与开始按钮。

状态不是 published 时显示未开放。

### POST /survey/公开编号/start

| 字段 | 说明 |
| --- | --- |
| anonymous | 可选；问卷允许匿名且出现时匿名提交 |

服务端行为：

- 校验问卷状态与截止时间。
- 检查 allow_multiple，必要时按 respondent_key 阻止重复提交。
- 创建 in_progress 答卷。
- 生成随机访问令牌，只在签名会话中保存原始令牌，数据库保存哈希。
- 返回 302 到 /attempt/答卷ID。

### GET /attempt/答卷ID

返回当前题目页面。

权限：会话中必须持有该答卷的访问令牌；否则 404。  
状态：

- in_progress：显示 current_question_id 对应的题目。
- submitted：重定向到完成页。
- 问卷关闭：显示关闭页，不再接受作答。

### POST /attempt/答卷ID/answer

| 字段 | 说明 |
| --- | --- |
| answer | 单选/文本/数字为一个字符串；多选可重复该字段 |

服务端行为：

1. 校验当前题目和访问令牌。
2. 调用题型校验；必答、多选数量、文本长度、数字范围与整数限制。
3. 校验失败返回 422 与题目页面，保留用户输入并显示错误。
4. 校验成功后将本题答案写入 answers，替换同一 question_id 的旧值。
5. 按 jumps 顺序匹配条件；命中 END 则提交，否则跳到目标题；未命中则下一题。
6. 302 到下一题或完成页。

### GET /attempt/答卷ID/complete

只有 submitted 状态可查看。返回提交成功页。

## 5. 健康检查

### GET /health

成功：

    { "status": "ok", "database": "connected" }

数据库不可用：503

    { "status": "error", "database": "unavailable" }

## 6. 错误处理

| 状态码 | 场景 |
| --- | --- |
| 400 | 参数不合法、已关闭问卷的非法修改 |
| 404 | 问卷、题目、答卷不存在；无权访问管理页或答卷 |
| 422 | 答案不满足题目校验 |
| 500 | 未捕获服务器错误 |

## 7. 安全说明

- 密码使用哈希，不保存明文。
- 管理接口通过 owner_id 做对象级权限检查。
- 公开链接使用随机 public_id，不使用递增数据库 ID。
- 答卷操作使用会话内随机令牌与数据库中的 SHA-256 哈希比对，防止答卷 ID 被猜测后篡改。
- 登录后的 next 参数只接受站内相对地址，避免开放重定向。
- 生产环境还应增加 CSRF Token、HTTPS、登录限流与安全响应头；这些属于第二阶段或部署加固范围。
