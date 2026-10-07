# nat-sim

完全本地的 NAT 与 Connection Tracking 模拟器。所有 packet、映射、端口状态和
时间信息只存在于进程内存中：**不修改真实防火墙、不创建网络命名空间、不连接
任何外部网络**。Packet 只是普通数据对象，测试全部在终端内完成。

## 结构

- `nat_sim/packet.py` — `Packet`、`FiveTuple`、`Protocol`(TCP/UDP)、`Direction`
- `nat_sim/clock.py` — 可注入时钟：`ManualClock`（测试用）与 `SystemClock`
- `nat_sim/conntrack.py` — `NatEngine`、`NatConfig`、conntrack 表项
- `tests/test_nat.py` — pytest 自动化测试

## Flow key（五元组）

每条流由五元组唯一标识：

```
(protocol, src_ip, src_port, dst_ip, dst_port)
```

conntrack 为每条流维护两个方向的映射：

- **正向**：内部五元组 → 分配的外部源端口
- **反向**：`(protocol, remote_ip, remote_port, external_ip, external_port)`
  → 原始内部地址，用于精确恢复入向 packet 的内部目标

同一活跃 flow 的后续 packet 命中正向映射并**复用同一外部端口**；不同内部
连接即使源端口相同，也会分配到不同外部端口而互不混淆。TCP 与 UDP 使用
相互独立的映射与端口池（相同五元组、不同协议是两条流）。

## 端口分配

- 端口范围通过 `NatConfig(port_range_start, port_range_end)` 配置（含边界）。
- 新 flow 总是分配范围内**最小的空闲端口**。
- 范围耗尽时抛出 `PortExhaustedError`，明确失败且不产生任何残留状态。

## 超时规则

- 每条 flow 记录 `last_seen`，每次正向或反向 packet 命中都会刷新。
- TCP 与 UDP 分别使用 `tcp_idle_timeout` / `udp_idle_timeout`。
- 空闲时间达到超时值的 flow 被移除，外部端口立即释放、可被新 flow 重用。
- 过期是惰性 + 主动两路触发：`process()` 每次处理 packet 前自动清理，
  也可以显式调用 `expire()`。
- 时间完全来自注入的 clock，测试中用 `ManualClock.advance()` 模拟时间流逝。

## 反向转换

入向 packet 必须精确匹配反向五元组（对端地址、对端端口、外部端口都要一致），
否则抛出 `NoMappingError`，**不会被错误转发**。flow 过期后其反向映射一并删除，
迟到的 packet 同样被拒绝。

## 运行测试

```bash
cd /mnt2/zjh/code/Goleta/session_94/b
python3 -m pytest -q
```

测试覆盖：多内部主机、相同源端口区分、双向转换与 payload 保留、无映射
packet 拒绝、端口耗尽显式失败、TCP/UDP 独立映射、idle timeout 过期、
活跃刷新计时器、端口释放与重用、惰性过期。
