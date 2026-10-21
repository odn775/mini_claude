"""内置工具定义（OpenAI function-calling 格式）。

只放「对模型可见的工具 schema」，实现见 builtin.py，注册与分发见 registry.py。
"""

from app.core.departments import names as department_names

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取指定路径的文件内容并返回",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要读取的文件绝对路径",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "将内容写入指定路径的文件，如果文件已存在则覆盖",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要写入的文件绝对路径",
                    },
                    "content": {
                        "type": "string",
                        "description": "要写入的文本内容",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_bash",
            "description": "执行一条 bash 命令并返回标准输出和标准错误",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "要执行的 shell 命令",
                    }
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "glob_search",
            "description": "使用 glob 模式搜索匹配的文件路径",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "glob 模式，如 **/*.py、src/**/*.ts",
                    }
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep_search",
            "description": "在文件中搜索匹配正则表达式的内容行",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "正则表达式模式",
                    },
                    "path": {
                        "type": "string",
                        "description": "搜索的起始目录，默认为当前工作目录",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_knowledge",
            "description": (
                "检索医学知识库（各科室适用症状、鉴别提示、就诊注意事项）。"
                "当需要判断症状对应哪个科室，或需要就诊准备信息时使用。"
                "query 会经过语义匹配，不需要精确关键词。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索查询，描述你需要查找的内容",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recommend_department",
            "description": (
                "给出分诊结论：推荐就诊科室、紧急程度与就诊建议。"
                "信息足够时必须调用此工具，不要在正文里代替它。"
                "科室名只能取自系统提示词里的科室清单。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "primary_department": {
                        "type": "string",
                        "enum": department_names(),
                        "description": "主推科室",
                    },
                    "alternate_department": {
                        "type": "string",
                        "enum": department_names() + [""],
                        "description": "备选科室，没有就留空字符串",
                    },
                    "urgency": {
                        "type": "string",
                        "enum": ["routine", "soon", "emergency"],
                        "description": (
                            "紧急程度：routine 普通门诊；"
                            "soon 24-48 小时内尽快就诊；emergency 立即急诊"
                        ),
                    },
                    "reasoning": {
                        "type": "string",
                        "description": "1-2 句分诊依据，尽量引用用户原话",
                    },
                    "advice": {
                        "type": "string",
                        "description": "就诊建议：挂号类型、是否空腹、需携带资料等",
                    },
                },
                "required": [
                    "primary_department",
                    "urgency",
                    "reasoning",
                    "advice",
                ],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_skill",
            "description": "读取完整 skill 指令。当用户的问题需要调用某个 skill 时，用此工具读取完整内容。",
            "parameters": {
                "type": "object",
                "properties": {
                    "skill_name": {
                        "type": "string",
                        "description": "skill 名称，如 translate、summarize、code-review",
                    }
                },
                "required": ["skill_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_health",
            "description": (
                "检查各模块状态（config/api/mcp/knowledge/tools/skills），"
                "返回各模块的健康状况。当某个工具调用报错或功能异常时，"
                "用此工具定位问题出在哪个模块。"
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]
