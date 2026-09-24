"""Redis 键空间约定（console / router / agent 三组件共享）。

节点      hs:node:{node_id}          hash   hostname/gpu/fw/status/last_seen        TTL=3×心跳
模型索引  hs:model:{model_name}      set    成员 = {node_id}:{port}
实例      hs:inst:{node_id}:{port}   hash   model/framework/url/metrics(status)     TTL=3×心跳
会话      hs:sess:{session_id}       hash   model/bound_inst/history(json)/created  TTL 滑动
历史      hs:sesshist:{session_id}   list   消息 json（重放缓存）                   随会话滑动
事件      hs:events                  list   集群事件流（环形 200 条）
租户      hs:tenant:{id}             hash   name/keys(集合)/pools/models/qps_limit
凭证      hs:cred:{node_id}          string AES-GCM(主密钥派生) 密文               控制台管理
"""

NODE_PREFIX = "hs:node:"
MODEL_PREFIX = "hs:model:"
INST_PREFIX = "hs:inst:"
SESS_PREFIX = "hs:sess:"
SESSHIST_PREFIX = "hs:sesshist:"
EVENTS_KEY = "hs:events"
TENANT_PREFIX = "hs:tenant:"
CRED_PREFIX = "hs:cred:"
