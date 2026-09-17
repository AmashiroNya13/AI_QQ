# Ecobot 行为内核

Ecobot 复用 AstrBot 的桌面壳、Python 运行时、平台、模型、插件、工具、日志、Dashboard 与发送链路，只替换行为决策中枢。AstrBot 负责可靠执行，Ecobot 负责理解世界并决定是否行动。

## 名称与职责

| Ecobot 名称 | 职责 | AstrBot 对接位置 |
| --- | --- | --- |
| 智能体核心 | 组装行为服务并协调生命周期 | `EcobotBehaviorStage` |
| 刷新调度器 | 防抖、批次编号、空闲到期与频道串行 | `ecobot_refresh_channels` / `ecobot_behavior_batches` |
| 世界模型 | 保存频道事实、关系、行为及反馈 | `world.db` |
| L1 近期记忆 | 保存并检索近期原始 QQ 对话 | `qq_messages` |
| L2 语义记忆 | 检索长期事实、关系、行为和动作结果 | `ecobot_memories` + FTS5 + Embedding Provider |
| 工作上下文 | 为本次思考选择、压缩和过滤材料 | Context Manager |
| 心跳处理器 | 执行统一认知批次 | 消息与空闲驱动共用入口 |
| 流式思考型 | `observe -> analyze_infer -> desire -> plan` | Model Provider |
| thoughts consumer | 校验并保存结构化认知结果 | Ecobot Kernel |
| 聊天调度器 | 仲裁沉默、表达、工具、延迟与主动行为 | Behavior Bridge |
| actions consumer | 调用 AstrBot 工具和消息执行器 | Agent Tool Loop / Respond Stage |
| 行为反思 | 消费真实动作反馈并决定结束或重规划 | Agent Tool Loop feedback |
| 防重复核心机制 | 对表达进行精确、模糊和可选向量去重 | `ecobot_expressions` / Respond Stage |

## 目标闭环

```text
事件
-> 刷新调度器
-> 世界模型
-> L1/L2/关系记忆
-> 心跳处理器
-> observe
-> analyze_infer
-> desire
-> plan
-> 聊天调度器
-> actions consumer
-> action_feedback
-> 行为反思
   -> reobserve / replan，或完成
-> 世界模型原子提交
```

## 运行结构

- `EcobotArchiveStage` 位于所有过滤之前，原样保存 OneBot 事件并启动非阻塞事实同步
- `WakingCheckStage` 只提供唤醒优先级和插件命令识别，不再充当 Ecobot 的认知开关
- `EcobotBehaviorStage` 对非插件消息执行刷新调度器和统一心跳，设置 `call_llm=True` 禁止 AstrBot 默认 LLM 重复决策
- `ResultDecorateStage` 与 `RespondStage` 继续负责内容安全、格式修饰和平台发送
- Pipeline 重载、删除配置、重启和退出都会关闭后台同步、空闲任务和数据库连接

## 持续状态

`ecobot_agent_states` 保存当前活动、行为、场景、地点、关注对象、同伴、情绪、能量、饥饿、疲劳、社交驱动力、目标、开始时间和预计结束时间。每次认知前会推进已到期活动，每次计划只能通过带版本检查的状态更新提交，所有变化和活动周期都保留历史。

## QQ 事实库

QQ 号和群号是稳定主键。数据库保留原始事件、消息段、提及、回复、撤回、昵称和群名变化、群名片、角色、成员进出周期、头像二进制、用户与群快照、好友与群清单、群历史回填、空间动态、消息媒体、出站消息及失败、关系证据、定期关系评估和版本化关系记忆。空间同步只通过可插拔 `QZoneSource`，未配置可靠数据源时不虚构接口。

## 管理入口

- Dashboard 页面：`/ecobot`
- API：`/api/v1/ecobot/status`
- 状态、状态历史、批次、关系、记忆、消息全文检索和配置均位于 `/api/v1/ecobot/*`
- 页面和 API 复用 AstrBot 登录与 `data` / `config` 权限
