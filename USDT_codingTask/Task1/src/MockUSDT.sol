// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title MockUSDT
/// @notice 用于本地测试的模拟 USDT 代币，遵循 ERC20 标准，小数位为 6（与真实 USDT 一致）。
/// @dev 不依赖 OpenZeppelin，内部手写 ERC20 核心逻辑，方便在没有外部库的情况下直接编译测试。
///      参考来源：
///      - ERC20 标准 EIP-20: https://eips.ethereum.org/EIPS/eip-20
///      - 真实 USDT (TetherToken) 合约（小数位 6）: https://etherscan.io/address/0xdAC17F958D2ee523a2206206994597C13D831ec7#code
contract MockUSDT {
    // ───────────────────────── 元数据 ─────────────────────────
    string public name = "Mock Tether USD";
    string public symbol = "USDT";
    // 真实 USDT 的 decimals 是 6，而不是常见的 18。
    // 这意味着 1 USDT = 1_000_000（10^6）个最小单位。
    uint8 public constant decimals = 6;

    // ───────────────────────── 状态变量 ─────────────────────────
    uint256 public totalSupply;

    // 账户余额：地址 => 持有的最小单位数量。
    // 注意余额记录在“代币合约”里，而不是用户钱包里。
    mapping(address => uint256) public balanceOf;

    // 授权额度：owner => (spender => 允许 spender 动用的额度)。
    // 这是 approve / transferFrom 机制的核心存储。
    mapping(address => mapping(address => uint256)) public allowance;

    // ───────────────────────── 事件 ─────────────────────────
    // ERC20 标准要求的两个事件，链下监听余额变化与授权变化时使用。
    event Transfer(address indexed from, address indexed to, uint256 value);
    event Approval(address indexed owner, address indexed spender, uint256 value);

    /// @notice 部署时把初始供应量铸造给部署者。
    /// @param initialSupply 初始供应量（以最小单位计，例如 1000 USDT 应传 1000 * 10**6）。
    constructor(uint256 initialSupply) {
        _mint(msg.sender, initialSupply);
    }

    // ───────────────────────── 标准 ERC20 接口 ─────────────────────────

    /// @notice 直接转账：把调用者自己的代币转给 to。
    function transfer(address to, uint256 amount) external returns (bool) {
        _transfer(msg.sender, to, amount);
        return true;
    }

    /// @notice 授权：允许 spender 在未来从“我”的余额里最多转走 amount。
    /// @dev 授权本身不移动任何代币，只是写入 allowance。
    function approve(address spender, uint256 amount) external returns (bool) {
        allowance[msg.sender][spender] = amount;
        emit Approval(msg.sender, spender, amount);
        return true;
    }

    /// @notice 代扣转账：spender（msg.sender）从 from 的余额里转 amount 给 to。
    /// @dev 需要 from 事先 approve 过 msg.sender，且额度足够。
    function transferFrom(address from, address to, uint256 amount) external returns (bool) {
        uint256 allowed = allowance[from][msg.sender];
        require(allowed >= amount, "ERC20: insufficient allowance");

        // 如果不是无限授权（type(uint256).max），则扣减额度。
        if (allowed != type(uint256).max) {
            allowance[from][msg.sender] = allowed - amount;
        }

        _transfer(from, to, amount);
        return true;
    }

    // ───────────────────────── 测试辅助函数 ─────────────────────────

    /// @notice 公开的铸造函数，仅供测试中给任意地址发币使用（真实 USDT 不会这样开放）。
    function mint(address to, uint256 amount) external {
        _mint(to, amount);
    }

    // ───────────────────────── 内部实现 ─────────────────────────

    function _transfer(address from, address to, uint256 amount) internal {
        require(to != address(0), "ERC20: transfer to zero address");
        require(balanceOf[from] >= amount, "ERC20: transfer amount exceeds balance");

        // Solidity 0.8+ 自带溢出检查，这里的加减是安全的。
        balanceOf[from] -= amount;
        balanceOf[to] += amount;

        emit Transfer(from, to, amount);
    }

    function _mint(address to, uint256 amount) internal {
        require(to != address(0), "ERC20: mint to zero address");
        totalSupply += amount;
        balanceOf[to] += amount;
        emit Transfer(address(0), to, amount);
    }
}
