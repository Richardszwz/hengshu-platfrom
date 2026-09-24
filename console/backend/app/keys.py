"""Redis 键空间约定（console / router / agent 三组件共享）。"""

NODE_PREFIX = "hs:node:"
MODEL_PREFIX = "hs:model:"
INST_PREFIX = "hs:inst:"
SESS_PREFIX = "hs:sess:"
SESSHIST_PREFIX = "hs:sesshist:"
EVENTS_KEY = "hs:events"
TENANT_PREFIX = "hs:tenant:"
CRED_PREFIX = "hs:cred:"

# QPS/时序采样（控制台 series 端点差分与环形缓冲）
STAT_REQ_KEY = "hs:stat:req"          # 累计请求计数（Router INCR）
SER_QPS_KEY = "hs:ser:qps"            # list: 每秒请求数样本
SER_TTFT_KEY = "hs:ser:ttft"          # list: 平均 TTFT 样本
SER_TPS_KEY = "hs:ser:tps"            # list: 集群吞吐样本
SER_UTIL_PREFIX = "hs:ser:util:"      # list: 单节点 GPU util 样本
