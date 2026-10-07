# NAT & Connection Tracking Simulator

一个完全本地的 NAT (NAPT) 与 Connection Tracking 模拟器。所有 packet、
映射、端口状态和时间信息只存在于内存中——不修改真实系统防火墙、不创建
网络命名空间、不连接任何外部网络。

## 结构

- `nat_sim/packet.py` — `Packet`（不可变五元组）与 `Protocol`（TCP/UDP）
- `nat_sim/clock.py` — 可注入时钟：`SystemClock` / `ManualClock`
- `nat_sim/nat.py` — `NATSimulator`：端口分配、conntrack、超时回收
- `tests/test_nat.py` — 自动化测试

## Flow key（五元组）

每条流由五元组唯一标识：

```
(protocol, internal_ip, internal_port, remote_ip, remote_port)
```

- 正向映射：flow key → 外部源端口
- 反向映射：`(protocol, remote_ip, remote_port, external_port)` → flow key

因此同一内部主机到不同远端、或不同内部主机使用相同源端口，都会得到
相互独立的映射；TCP 与 UDP 的五元组也互不影响。

## 端口分配

- 构造时通过 `port_range=(lo, hi)` 配置外部源端口范围（默认 40000–60999）。
- 新 flow 按升序分配范围内第一个空闲端口；同一活跃 flow 的后续 packet
  复用已有映射，不会重复分配。
- 范围耗尽时抛出 `PortExhaustedError`，明确失败。

## 超时规则

- 每个 conntrack 条目记录 `last_seen`；任意方向（出向或入向）的 packet
  命中该条目都会刷新它。
- 当 `now - last_seen >= idle_timeout` 时条目过期，外部端口被回收并可
  重新分配。
- 过期采用惰性清扫：在 `translate_outbound` / `translate_inbound` /
  `active_flows` 调用时触发。
- 时间通过注入的 `clock` 获取；测试用 `ManualClock` 手动推进，无需真实
  等待。

## 反向转换

`translate_inbound(packet)` 依据 conntrack 精确还原内部目标
`(internal_ip, internal_port)`。没有对应映射、或目的地址不是 NAT 公网
IP 的 packet 一律丢弃（返回 `None`），不会被错误转发。

## 用法示例

```python
from nat_sim import ManualClock, NATSimulator, Packet, Protocol

clock = ManualClock()
nat = NATSimulator(
    public_ip="198.51.100.1",
    port_range=(40000, 40010),
    idle_timeout=60.0,
    clock=clock,
)

out = nat.translate_outbound(Packet(Protocol.TCP, "10.0.0.2", 12345, "203.0.113.7", 80))
# -> src 改写为 (198.51.100.1, 40000)

reply = nat.translate_inbound(Packet(Protocol.TCP, "203.0.113.7", 80, "198.51.100.1", out.src_port))
# -> dst 还原为 (10.0.0.2, 12345)

clock.advance(61.0)   # 超过 idle_timeout，flow 过期，端口回收
```

## 运行测试

```bash
python3 -m pytest tests/ -v
```

测试覆盖：多内部主机、相同源端口区分、双向转换、端口耗尽、TCP/UDP
独立映射、idle timeout、端口回收重用、活跃刷新超时、无映射丢包。
全部在内存中执行，不接触真实网络配置。
