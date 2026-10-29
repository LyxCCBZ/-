# 简问：基于 MongoDB 的在线问卷系统（第二阶段）

本项目实现了课程大作业第一、第二阶段要求的在线问卷系统。系统允许注册用户创建问卷、添加题目、配置条件跳转、发布链接、收集答卷并查看自动统计结果；第二阶段新增题库、题目共享、不可变版本、历史版本恢复与跨问卷统计。所有业务数据均存储在 MongoDB 中。

## 已实现功能

- 用户注册、登录、退出；密码使用 Werkzeug 默认的 scrypt 哈希保存。
- 用户隔离：登录用户只能管理自己创建的问卷。
- 问卷创建、说明、匿名填写开关、重复填写开关、截止时间、发布、关闭。
- 三种基础题型及四种输入控件：单选、多选、文本填空、数字填空。
- 必答校验；多选最少/最多数量；文本最少/最多字数；数字范围与整数限制。
- 可配置条件跳转：单选、多选、数字、文本均支持按条件跳到后续题目或直接结束。
- 逐题填写、服务端跳转判断；每次答题后即时保存草稿；提交后计入统计。
- 匿名提交、重复提交限制、多人并行填写。
- 统计：总提交数、单选/多选各选项计数与百分比、文本全部内容、数字平均值/最小值/最大值。
- 整卷统计、单题统计视图、JSON 统计 API。
- 题库：把常用题目保存为可复用题目，支持新建、管理和按版本选用。
- 题目共享：按用户名共享题库题目；被共享用户可在自己的问卷中使用，也可复制为自有题目。
- 版本化：修改题库题目只创建新版本，不覆盖旧内容；支持 v1→v2→v3 关系展示与恢复旧版本。
- 快照隔离：从题库选题时复制指定版本到问卷，已发布问卷和旧数据不受后续改版影响。
- 同一题目的不同版本可同时被不同问卷使用。
- 使用情况：查看某道题被哪些问卷使用、使用的版本和回答量。
- 跨问卷题目统计：按题目身份汇总多份问卷的选项、数字与文本回答；共享用户只能查看自己问卷范围。
- MongoDB JSON Schema 校验、唯一索引与查询索引。
- 29 项 pytest 自动化测试（22 项第一阶段回归 + 7 项第二阶段新增）和完整项目文档。

## 技术栈

- Python 3.10+
- Flask 3.1
- PyMongo 4.15
- MongoDB 7/8
- Jinja2 服务端模板、原生 HTML/CSS/JavaScript
- pytest

## 目录结构

    questionnaire-system/
      app.py                  Flask 应用工厂与错误处理
      config.py               环境配置
      db.py                   MongoDB 连接、Schema Validator、索引
      security.py             密码、登录态、登录保护
      utils.py                ObjectId、时间、JSON 工具
      routes/
        auth.py               注册、登录、退出
        surveys.py            问卷、题目、跳转、统计、API
        question_bank.py      题库、共享、版本、恢复、跨问卷统计
        public.py             公开链接、逐题填写、答卷保存
      services/
        validation.py         题目与答案校验、跳转条件解析
        rules.py              条件匹配与下一题计算
        statistics.py         MongoDB 聚合统计
        question_bank.py      题库版本、快照复制、使用查询、跨问卷统计
      templates/              Jinja2 页面
      static/style.css        页面样式
      tests/                  单元测试与集成测试
      scripts/seed_demo.py    演示数据
      docs/                   设计、API、测试、AI 工具记录、项目报告
      requirements.txt
      .env.example

## 运行步骤

### 1. 启动 MongoDB

默认连接地址为 mongodb://127.0.0.1:27017。

Windows 示例：

    mongod --dbpath D:\MongoDB\data --bind_ip 127.0.0.1 --port 27017

也可以使用已有 MongoDB 服务或 MongoDB Atlas。若使用其他地址，请设置 MONGODB_URI。

### 2. 安装依赖

    python -m venv .venv
    .venv\Scripts\activate
    pip install -r requirements.txt

Linux/macOS 激活命令为 source .venv/bin/activate。

### 3. 配置环境变量

如果使用默认的本地 MongoDB，可以跳过本步骤，直接使用项目内置默认值：

    mongodb://127.0.0.1:27017
    questionnaire_phase1

PowerShell 示例：

    $env:SECRET_KEY="replace-with-a-long-random-string"
    $env:MONGODB_URI="mongodb://127.0.0.1:27017"
    $env:MONGODB_DB="questionnaire_phase1"

Command Prompt（cmd.exe）示例：

    set "SECRET_KEY=replace-with-a-long-random-string"
    set "MONGODB_URI=mongodb://127.0.0.1:27017"
    set "MONGODB_DB=questionnaire_phase1"

注意：cmd.exe 不支持 `$env:变量名=值` 语法；如果在 cmd 中执行 PowerShell 写法，会出现“文件名、目录名或卷标语法不正确”。

环境变量均有开发默认值，但正式演示前应设置独立的 SECRET_KEY。

### 4. 启动应用

    flask --app app run --host 127.0.0.1 --port 5000

或：

    python app.py

访问 http://127.0.0.1:5000/ ，首次使用请先注册账号。

### 5. 导入演示数据（可选）

    python scripts/seed_demo.py

演示账号：demo / demo123  
演示填写链接一：http://127.0.0.1:5000/survey/demo-learning-survey  
演示填写链接二：http://127.0.0.1:5000/survey/demo-club-survey

演示数据包含一道被两份问卷复用的题库题目，登录后可在“题库”页查看版本与跨问卷统计。

## 测试

测试默认使用独立的 MongoDB 实例地址 mongodb://127.0.0.1:27018，避免污染业务数据库。可先启动测试实例：

    mongod --dbpath D:\MongoDB\test-data --bind_ip 127.0.0.1 --port 27018

然后执行：

    $env:TEST_MONGODB_URI="mongodb://127.0.0.1:27018"
    python -m pytest

本机验证结果：

    29 passed

测试覆盖账号隔离、问卷创建、题目校验、发布锁定、条件跳转、多选/文本/数字校验、答卷保存、统计、重复提交限制、关闭问卷、匿名隐私边界、安全重定向，以及题库保存、共享权限、版本历史恢复、快照隔离和跨问卷统计等流程。

## 关键设计

- MongoDB 集合拆分：users、surveys、questions、responses、question_bank、question_versions。
- 题目按 survey_id 引用问卷；答卷 answers 内嵌保存本题作答快照。
- 题库采用“逻辑题目 + 不可变版本 + 问卷快照”三层模型：question_bank 保存身份和当前版本指针，question_versions 保存不可变内容，questions 保存问卷实际使用的版本快照。
- 修改题库题目只新增版本；恢复历史版本也会生成新版本，历史链不丢失。
- 跨问卷统计按 library_question_id 聚合，选择题按“选项编号 + 选项文本”合并。
- 跳转规则保存在题目文档中，由服务端统一解释执行，不写死在程序逻辑里。
- 问卷发布后锁定题目和跳转结构；设置仍可调整，关闭后不可编辑。
- 匿名答卷不保存 respondent_id，但保存不可逆的 respondent_key，用于执行“禁止重复提交”规则。
- 统计使用 MongoDB aggregation 的 match、unwind、group 阶段汇总作答值。

详细说明见 docs 目录。

## 已知边界

- 题库题目暂不支持删除、回收站、批量导入导出和拖拽排序；草稿问卷可通过删除后重选的方式换题。
- 已发布的问卷仍然锁定题目结构，不支持在线换题；这是保护历史数据的刻意设计。
- 同一账号重复填写由 respondent_key 限制；匿名模式仍使用不可逆键去重，但不展示身份。
- 进度条按题目原始序号显示；发生跳转时，答题数量可能少于总题数，这是条件问卷的正常现象。
- 生产部署应增加 HTTPS、CSRF 防护、登录限流和生产级 WSGI 服务。
- 为兼容第一阶段已有数据，默认数据库名仍为 questionnaire_phase1；可通过 MONGODB_DB 修改。

## 文档索引

- docs/系统说明.md
- docs/数据库设计.md
- docs/API说明.md
- docs/关键逻辑说明.md
- docs/测试用例.md
- docs/AI使用日志.md（AI 工具使用记录）
- docs/项目完成报告.md
- docs/第二阶段项目完成报告.md
