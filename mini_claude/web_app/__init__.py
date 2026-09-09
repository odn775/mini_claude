"""Mini Claude Web 分层子包。

Web 拆分后的三层结构（统一由 web.py 薄壳入口聚合）：
- state.py    全局状态 + ChatSession 模型
- service.py  业务功能（会话辅助、工具执行、知识库）
- routers.py  APIRouter 接口层（仅做薄封装，调 service）
"""