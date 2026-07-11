// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @notice 任务要求的参考接口（ERC20 子集）。
/// @dev 真实 USDT 的 transfer/transferFrom 在主网上并不返回 bool，
///      但本接口按任务给定写法声明返回 bool，配合下方 _safeTransfer*/低层 call 兼容两种情况。
interface IERC20 {
    function transfer(address to, uint256 amount) external returns (bool);

    function transferFrom(address from, address to, uint256 amount) external returns (bool);

    function balanceOf(address account) external view returns (uint256);
}

/// @title USDTTimeLock
/// @notice USDT 时间锁托管合约：用户存入 USDT 后，必须等待至少 1 天才能取回，且只有存款人本人可取回。
/// @dev 设计要点：
///      1. 采用「检查-生效-交互」(Checks-Effects-Interactions) 模式：取款时先清空存款记录，再做外部转账，防重入。
///      2. 用低层 call 封装 transfer/transferFrom，兼容真实 USDT「不返回 bool」的非标准实现。
///      参考来源：
///      - 时间限制使用 block.timestamp，参考 Solidity 文档 Units & Globals:
///        https://docs.soliditylang.org/en/latest/units-and-global-variables.html#block-and-transaction-properties
///      - 安全转账思路参考 OpenZeppelin SafeERC20:
///        https://github.com/OpenZeppelin/openzeppelin-contracts/blob/master/contracts/token/ERC20/utils/SafeERC20.sol
///      - CEI / 重入防护参考 Solidity 安全建议:
///        https://docs.soliditylang.org/en/latest/security-considerations.html#reentrancy
contract USDTTimeLock {
    // ───────────────────────── 常量与不可变变量 ─────────────────────────

    /// @notice 锁定时长：1 天。改成 1 小时只需把这里改为 1 hours。
    uint256 public constant LOCK_PERIOD = 1 days;

    /// @notice 被托管的 USDT 代币地址，部署后不可更改。
    IERC20 public immutable usdt;

    // ───────────────────────── 数据结构 ─────────────────────────

    /// @notice 一条存款记录。
    /// @dev 本实现选择「覆盖式」单笔存款（见文档思考题1的说明），
    ///      重复 deposit 会把金额累加到同一条记录，并刷新解锁时间。
    struct DepositInfo {
        uint256 amount;       // 当前托管的 USDT 数量（最小单位）
        uint256 depositTime;  // 最近一次存款时间（block.timestamp）
        uint256 unlockTime;   // 可取回时间 = depositTime + LOCK_PERIOD
    }

    /// @notice 用户地址 => 其存款记录。
    mapping(address => DepositInfo) private deposits;

    // ───────────────────────── 事件 ─────────────────────────
    event Deposited(address indexed user, uint256 amount, uint256 depositTime, uint256 unlockTime);
    event Withdrawn(address indexed user, uint256 amount);

    // ───────────────────────── 构造函数 ─────────────────────────

    /// @param _usdt USDT（或 MockUSDT）合约地址。
    constructor(address _usdt) {
        require(_usdt != address(0), "USDT address is zero");
        usdt = IERC20(_usdt);
    }

    // ───────────────────────── 核心函数 ─────────────────────────

    /// @notice 存入 USDT。调用前用户必须先对本合约 approve 足够额度。
    /// @param amount 存入数量（最小单位，6 位小数）。
    function deposit(uint256 amount) external {
        require(amount > 0, "amount must be > 0");

        DepositInfo storage info = deposits[msg.sender];

        // ── Effects：先更新合约内部记录 ──
        // 覆盖式：累加金额，并以本次存款时间重置锁定周期。
        info.amount += amount;
        info.depositTime = block.timestamp;
        info.unlockTime = block.timestamp + LOCK_PERIOD;

        // ── Interactions：从用户账户把 USDT 划转到本合约 ──
        // 需要用户事先 approve，否则此处会因额度不足而 revert。
        _safeTransferFrom(msg.sender, address(this), amount);

        emit Deposited(msg.sender, amount, info.depositTime, info.unlockTime);
    }

    /// @notice 取回自己的全部 USDT，需满足：有存款 且 已过锁定期。
    function withdraw() external {
        DepositInfo memory info = deposits[msg.sender];

        // ── Checks ──
        require(info.amount > 0, "no deposit");
        require(block.timestamp >= info.unlockTime, "still locked");

        uint256 amount = info.amount;

        // ── Effects：先清除记录，防止重入与重复取款 ──
        delete deposits[msg.sender];

        // ── Interactions：最后才把代币转出 ──
        _safeTransfer(msg.sender, amount);

        emit Withdrawn(msg.sender, amount);
    }

    // ───────────────────────── 查询函数 ─────────────────────────

    /// @notice 查询某用户的存款记录。
    /// @return amount 托管金额；depositTime 存款时间；unlockTime 可取回时间。
    function getDeposit(address user)
        external
        view
        returns (uint256 amount, uint256 depositTime, uint256 unlockTime)
    {
        DepositInfo memory info = deposits[user];
        return (info.amount, info.depositTime, info.unlockTime);
    }

    // ───────────────────────── 内部安全转账 ─────────────────────────

    /// @dev 用低层 call 调用 transfer，兼容「返回 bool」与「无返回值」两种 USDT 实现。
    ///      若调用失败或显式返回 false，则 revert。
    function _safeTransfer(address to, uint256 amount) internal {
        (bool ok, bytes memory data) =
            address(usdt).call(abi.encodeWithSelector(IERC20.transfer.selector, to, amount));
        require(ok && (data.length == 0 || abi.decode(data, (bool))), "USDT transfer failed");
    }

    /// @dev 同上，封装 transferFrom。
    function _safeTransferFrom(address from, address to, uint256 amount) internal {
        (bool ok, bytes memory data) =
            address(usdt).call(abi.encodeWithSelector(IERC20.transferFrom.selector, from, to, amount));
        require(ok && (data.length == 0 || abi.decode(data, (bool))), "USDT transferFrom failed");
    }
}
