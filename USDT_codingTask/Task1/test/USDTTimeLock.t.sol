// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {Test} from "forge-std/Test.sol";
import {MockUSDT} from "../src/MockUSDT.sol";
import {USDTTimeLock} from "../src/USDTTimeLock.sol";

/// @title USDTTimeLock 测试
/// @notice 使用 Foundry 的 forge-std Test 基类编写，覆盖任务三要求的全部场景。
/// @dev 测试工具来源与参考：
///      - forge-std Test（提供 vm.prank / vm.warp / assertEq 等）:
///        https://github.com/foundry-rs/forge-std/blob/master/src/Test.sol
///      - vm.warp（推进区块时间）/ vm.prank（伪装 msg.sender）/ vm.expectRevert 等作弊码:
///        https://book.getfoundry.sh/cheatcodes/
///      代币数据来源：本地部署的 MockUSDT（6 位小数），非主网 fork。
contract USDTTimeLockTest is Test {
    MockUSDT internal usdt;
    USDTTimeLock internal lock;

    // 测试用账户：用 makeAddr 生成带标签的确定性地址，便于阅读 trace。
    address internal alice = makeAddr("alice");
    address internal bob = makeAddr("bob");

    // USDT 是 6 位小数，定义一个 1 USDT 的单位常量，提升可读性。
    uint256 internal constant ONE_USDT = 1e6;
    uint256 internal constant LOCK_PERIOD = 1 days;

    /// @notice 每个测试用例运行前都会执行：部署合约并给两个用户发币、预先授权。
    function setUp() public {
        // 部署 MockUSDT，初始供应给本测试合约（部署者）。
        usdt = new MockUSDT(1_000_000 * ONE_USDT);

        // 部署时间锁合约，绑定上面的 USDT。
        lock = new USDTTimeLock(address(usdt));

        // 给 alice、bob 各发 1000 USDT。
        usdt.mint(alice, 1000 * ONE_USDT);
        usdt.mint(bob, 1000 * ONE_USDT);

        // 用户提前对时间锁合约 approve，模拟前端「先授权再存款」的流程。
        vm.prank(alice);
        usdt.approve(address(lock), type(uint256).max);

        vm.prank(bob);
        usdt.approve(address(lock), type(uint256).max);
    }

    // ───────────────────────── 场景 1：成功存入 & 合约余额增加 ─────────────────────────

    /// @notice 用户成功存入 USDT，且时间锁合约中的 USDT 余额相应增加，记录被正确写入。
    function testDepositUSDT() public {
        uint256 amount = 100 * ONE_USDT;

        uint256 lockBalanceBefore = usdt.balanceOf(address(lock));
        uint256 aliceBalanceBefore = usdt.balanceOf(alice);

        vm.prank(alice);
        lock.deposit(amount);

        // 合约 USDT 余额增加 amount。
        assertEq(usdt.balanceOf(address(lock)), lockBalanceBefore + amount, "lock balance should increase");
        // alice 余额减少 amount。
        assertEq(usdt.balanceOf(alice), aliceBalanceBefore - amount, "alice balance should decrease");

        // 存款记录正确：金额、存款时间、解锁时间。
        (uint256 recAmount, uint256 depositTime, uint256 unlockTime) = lock.getDeposit(alice);
        assertEq(recAmount, amount, "recorded amount mismatch");
        assertEq(depositTime, block.timestamp, "depositTime mismatch");
        assertEq(unlockTime, block.timestamp + LOCK_PERIOD, "unlockTime mismatch");
    }

    // ───────────────────────── 场景 2：未满 1 天取款失败 ─────────────────────────

    /// @notice 存款后未满 1 天，立即取款应当 revert("still locked")。
    function testCannotWithdrawBeforeOneDay() public {
        uint256 amount = 100 * ONE_USDT;

        vm.prank(alice);
        lock.deposit(amount);

        // 时间只前进 1 天差 1 秒，仍处于锁定期。
        vm.warp(block.timestamp + LOCK_PERIOD - 1);

        vm.prank(alice);
        vm.expectRevert(bytes("still locked"));
        lock.withdraw();
    }

    // ───────────────────────── 场景 3：满 1 天后成功取回 & 记录清除 ─────────────────────────

    /// @notice 用 vm.warp 模拟时间经过 1 天后，用户成功取回 USDT，且存款记录被清空。
    function testWithdrawAfterOneDay() public {
        uint256 amount = 100 * ONE_USDT;
        uint256 aliceBalanceBefore = usdt.balanceOf(alice);

        vm.prank(alice);
        lock.deposit(amount);

        // 时间推进到恰好满 1 天（>= unlockTime 即可取回）。
        vm.warp(block.timestamp + LOCK_PERIOD);

        vm.prank(alice);
        lock.withdraw();

        // alice 余额恢复，合约余额清零。
        assertEq(usdt.balanceOf(alice), aliceBalanceBefore, "alice should get funds back");
        assertEq(usdt.balanceOf(address(lock)), 0, "lock should be empty");

        // 存款记录被清除（全部归零）。
        (uint256 recAmount, uint256 depositTime, uint256 unlockTime) = lock.getDeposit(alice);
        assertEq(recAmount, 0, "amount should be cleared");
        assertEq(depositTime, 0, "depositTime should be cleared");
        assertEq(unlockTime, 0, "unlockTime should be cleared");
    }

    /// @notice 取款后记录已清除，再次取款应失败（防重复取款）。
    function testCannotWithdrawTwice() public {
        uint256 amount = 100 * ONE_USDT;

        vm.prank(alice);
        lock.deposit(amount);

        vm.warp(block.timestamp + LOCK_PERIOD);

        vm.prank(alice);
        lock.withdraw();

        // 第二次取款，记录已空，应 revert("no deposit")。
        vm.prank(alice);
        vm.expectRevert(bytes("no deposit"));
        lock.withdraw();
    }

    // ───────────────────────── 场景 4：非本人不能取走他人 USDT ─────────────────────────

    /// @notice alice 存款后，bob 自己没有存款，调用 withdraw 只会因「no deposit」失败，
    ///         无法动用 alice 的资金（每个地址的存款相互独立）。
    function testOtherUserCannotWithdraw() public {
        uint256 amount = 100 * ONE_USDT;

        vm.prank(alice);
        lock.deposit(amount);

        vm.warp(block.timestamp + LOCK_PERIOD);

        // bob 没有存款，取款失败。
        vm.prank(bob);
        vm.expectRevert(bytes("no deposit"));
        lock.withdraw();

        // 确认 alice 的资金仍然安全地锁在合约里。
        (uint256 recAmount,,) = lock.getDeposit(alice);
        assertEq(recAmount, amount, "alice deposit must remain intact");
        assertEq(usdt.balanceOf(address(lock)), amount, "lock balance must remain intact");
    }

    // ───────────────────────── 补充：重复存款累加 & 刷新解锁时间 ─────────────────────────

    /// @notice 验证「覆盖式累加」语义：再次 deposit 会累加金额并刷新解锁时间。
    function testRepeatDepositAccumulatesAndRefreshesLock() public {
        vm.prank(alice);
        lock.deposit(100 * ONE_USDT);

        // 过半天后再存一笔。
        vm.warp(block.timestamp + 12 hours);

        vm.prank(alice);
        lock.deposit(50 * ONE_USDT);

        (uint256 recAmount,, uint256 unlockTime) = lock.getDeposit(alice);
        assertEq(recAmount, 150 * ONE_USDT, "amount should accumulate");
        // 解锁时间应基于第二次存款重新计算。
        assertEq(unlockTime, block.timestamp + LOCK_PERIOD, "unlock time should refresh on new deposit");
    }

    /// @notice 存款金额为 0 应被拒绝。
    function testDepositZeroReverts() public {
        vm.prank(alice);
        vm.expectRevert(bytes("amount must be > 0"));
        lock.deposit(0);
    }
}
