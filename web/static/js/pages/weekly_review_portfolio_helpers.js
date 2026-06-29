(function () {
  const MARKET_SUFFIXES = ['.HK', '.SH', '.SZ', '.SS', '.US', '.AS', '.DE', '.VI', '.T', '.KS', '.KQ'];
  const BUY_TYPES = new Set(['buy', 'Buy', 'add']);
  const SELL_TYPES = new Set(['sell', 'Sell', 'trim', 'Text']);

  function deepClone(value) {
    return value == null ? value : JSON.parse(JSON.stringify(value));
  }

  function numericShares(value) {
    const n = parseFloat(value);
    return Number.isFinite(n) ? n : null;
  }

  function primaryMarketCode(value, marketSuffixes = MARKET_SUFFIXES) {
    const upper = (value || '').toString().trim().toUpperCase();
    if (!upper) return '';
    for (const suffix of marketSuffixes) {
      if (upper.endsWith(suffix)) return upper.slice(0, -suffix.length);
    }
    return upper;
  }

  function marketCodeAliases(value, marketSuffixes = MARKET_SUFFIXES) {
    const raw = (value || '').toString().trim();
    const upper = raw.toUpperCase();
    if (!upper) return [];
    const aliases = [raw, upper, primaryMarketCode(upper, marketSuffixes)];
    for (const suffix of marketSuffixes) {
      if (upper.endsWith(suffix)) aliases.push(upper.slice(0, -suffix.length));
      else aliases.push(upper + suffix);
    }
    return [...new Set(aliases.filter(Boolean))];
  }

  function aliasSet(value, marketSuffixes = MARKET_SUFFIXES) {
    return new Set(marketCodeAliases(value, marketSuffixes).map((item) => item.toString().trim().toUpperCase()));
  }

  function aliasesIntersect(left, right) {
    for (const item of left) {
      if (right.has(item)) return true;
    }
    return false;
  }

  function stockDisplayCode(stock) {
    return (stock?.ticker || stock?.stock_id || '').toString().trim();
  }

  function edgeDisplayCode(edge) {
    return (edge?.stock_code || '').toString().trim();
  }

  function snapshotEntries(snapshot) {
    return Object.entries((snapshot || {}).stocks || {});
  }

  function snapshotStockForCode(snapshot, code, marketSuffixes = MARKET_SUFFIXES) {
    const aliases = aliasSet(code, marketSuffixes);
    for (const [sid, payload] of snapshotEntries(snapshot)) {
      const candidates = [sid, payload?.ticker, payload?.stock_id, payload?.stock_name];
      if (candidates.some((candidate) => aliasesIntersect(aliases, aliasSet(candidate, marketSuffixes)))) {
        return { sid, payload: payload || {} };
      }
    }
    return null;
  }

  function previousSharesForCode(snapshot, code, marketSuffixes = MARKET_SUFFIXES) {
    const row = snapshotStockForCode(snapshot, code, marketSuffixes);
    if (!row) return null;
    const shares = numericShares(row.payload?.shares_held);
    return shares == null ? 0 : shares;
  }

  function createPortfolioInteractionState(snapshot) {
    const savedSnapshot = deepClone(snapshot || { stocks: {} }) || { stocks: {} };
    return {
      savedSnapshot,
      editDraft: deepClone(savedSnapshot),
      rebalancingDraft: { ops: [] },
      appliedSnapshot: deepClone(savedSnapshot),
    };
  }

  function hasUnsavedShareEdits(state, marketSuffixes = MARKET_SUFFIXES) {
    const saved = (state || {}).savedSnapshot || {};
    const edit = (state || {}).editDraft || {};
    const codes = new Set([
      ...snapshotEntries(saved).map(([sid, payload]) => payload?.ticker || sid),
      ...snapshotEntries(edit).map(([sid, payload]) => payload?.ticker || sid),
    ]);
    for (const code of codes) {
      const before = previousSharesForCode(saved, code, marketSuffixes) || 0;
      const after = previousSharesForCode(edit, code, marketSuffixes) || 0;
      if (Math.abs(after - before) > 1e-9) return true;
    }
    return false;
  }

  function resetEditDraftFromApplied(state) {
    const next = state || createPortfolioInteractionState({ stocks: {} });
    next.editDraft = deepClone(next.appliedSnapshot || { stocks: {} });
    return next;
  }

  function normalizeRebalancingOp(o, idSeed) {
    return {
      _id: (o && o._id) || (`rb-${idSeed}-${Math.random().toString(36).slice(2)}`),
      stock_id: o?.stock_id || '',
      op_type: o?.op_type || 'buy',
      quantity: o?.quantity ?? '',
      price: o?.price ?? '',
      date: o?.date || '',
      note: o?.note || '',
      pairing_mode: o?.pairing_mode || 'auto',
      paired_buys: Array.isArray(o?.paired_buys) ? o.paired_buys.map((pair) => ({
        stock_id: pair?.stock_id || '',
        amount: pair?.amount ?? '',
        ratio: pair?.ratio ?? '',
        buy_week_id: pair?.buy_week_id || '',
        buy_date: pair?.buy_date || '',
        source: pair?.source || 'manual',
      })) : [],
      pairing_note: o?.pairing_note || '',
      decision_type: o?.decision_type || 'unknown',
      destination_type: o?.destination_type || 'unknown',
      review_horizon: o?.review_horizon || 'week_end',
      benchmark: o?.benchmark || '',
      decision_note: o?.decision_note || '',
      baseline_shares: o?.baseline_shares ?? null,
      baseline_stock_id: o?.baseline_stock_id || '',
      _source: o?._source || '',
    };
  }

  function buildRebalancingDraftsFromShareEdits({
    stocks = [],
    edgeHoldings = [],
    snapshot = { stocks: {} },
    marketSuffixes = MARKET_SUFFIXES,
  } = {}) {
    const drafts = [];
    const addDraft = (code, name, currentShares, price, note, baselineStockId = '') => {
      const previousShares = previousSharesForCode(snapshot, code, marketSuffixes);
      const baselineShares = previousShares == null ? 0 : previousShares;
      const current = numericShares(currentShares);
      if (current == null) return;
      const delta = current - baselineShares;
      if (Math.abs(delta) < 1e-9) return;
      drafts.push(normalizeRebalancingOp({
        stock_id: code,
        op_type: delta > 0 ? 'buy' : 'sell',
        quantity: Math.abs(delta),
        price: price || '',
        date: '',
        note,
        decision_type: 'position_reconciliation',
        destination_type: 'unknown',
        review_horizon: 'week_end',
        pairing_mode: 'auto',
        baseline_shares: baselineShares,
        baseline_stock_id: baselineStockId || code,
        _source: 'position_reconciliation',
      }, `draft-${code}-${drafts.length}`));
    };

    stocks.forEach((stock) => {
      addDraft(
        stockDisplayCode(stock),
        stock?.stock_name || stock?.stock_id || '',
        stock?.data?.shares_held,
        stock?.data?.performance_data?.end_price || stock?.data?.avg_cost || '',
        `TextHoldingsTextGenerate: ${stock?.stock_name || stock?.stock_id || ''}`,
        stock?.stock_id || ''
      );
    });
    edgeHoldings.forEach((edge) => {
      addDraft(
        edgeDisplayCode(edge),
        edge?.stock_name || edge?.stock_code || '',
        edge?.shares_held,
        edge?.performance_data?.end_price || edge?.avg_cost || '',
        `TextHoldingsTextGenerate: ${edge?.stock_name || edge?.stock_code || ''}`,
        edge?.stock_code || ''
      );
    });
    return drafts;
  }

  function buildRemoveHoldingDraft({
    stock,
    snapshot = { stocks: {} },
    marketSuffixes = MARKET_SUFFIXES,
  } = {}) {
    const code = stockDisplayCode(stock);
    const previousShares = previousSharesForCode(snapshot, code, marketSuffixes);
    if (previousShares == null || previousShares <= 0) return { draft: null };
    return {
      draft: normalizeRebalancingOp({
        stock_id: code,
        op_type: 'sell',
        quantity: previousShares,
        price: stock?.data?.performance_data?.end_price || stock?.data?.avg_cost || '',
        date: '',
        note: `TextHoldingsTextGenerate: ${stock?.stock_name || stock?.stock_id || ''}`,
        decision_type: 'position_reconciliation',
        destination_type: 'unknown',
        review_horizon: 'week_end',
        pairing_mode: 'auto',
        baseline_shares: previousShares,
        baseline_stock_id: stock?.stock_id || code,
        _source: 'position_reconciliation',
      }, `remove-${code}-${Date.now()}`),
    };
  }

  function isPositiveShares(value) {
    const shares = numericShares(value);
    return shares != null && shares > 0;
  }

  function hasAnySharesInput(value) {
    return value !== undefined && value !== null && String(value).trim() !== '';
  }

  function activeStocks({ stocks = [], snapshot = { stocks: {} }, closedPositions = [], marketSuffixes = MARKET_SUFFIXES } = {}) {
    const closedAliases = new Set((closedPositions || []).flatMap((item) => marketCodeAliases(item?.stock_id, marketSuffixes).map((v) => v.toUpperCase())));
    return (stocks || []).filter((stock) => {
      const code = stockDisplayCode(stock);
      if (!code) return false;
      if (marketCodeAliases(code, marketSuffixes).some((alias) => closedAliases.has(alias.toUpperCase()))) return false;
      return Boolean(snapshotStockForCode(snapshot, code, marketSuffixes));
    });
  }

  function activeEdgeHoldings({ edgeHoldings = [], closedPositions = [], marketSuffixes = MARKET_SUFFIXES } = {}) {
    const closedAliases = new Set((closedPositions || []).flatMap((item) => marketCodeAliases(item?.stock_id, marketSuffixes).map((v) => v.toUpperCase())));
    return (edgeHoldings || []).filter((edge) => {
      const code = edgeDisplayCode(edge);
      return code && !marketCodeAliases(code, marketSuffixes).some((alias) => closedAliases.has(alias.toUpperCase()));
    });
  }

  function displayStocks(options = {}) {
    return activeStocks(options).filter((stock) => !hasAnySharesInput(stock?.data?.shares_held) || isPositiveShares(stock?.data?.shares_held));
  }

  function displayEdgeHoldings(options = {}) {
    return activeEdgeHoldings(options).filter((edge) => !hasAnySharesInput(edge?.shares_held) || isPositiveShares(edge?.shares_held));
  }

  function buildRebalancingBaseHoldings({ ops = [], snapshot = { stocks: {} }, displayCodes = [], codeToStorageKey = {}, marketSuffixes = MARKET_SUFFIXES, includeSnapshot = true } = {}) {
    const baseHoldings = {};
    const allowedAliases = new Set((displayCodes || [])
      .flatMap((code) => marketCodeAliases(code, marketSuffixes))
      .map((code) => code.toString().trim().toUpperCase())
      .filter(Boolean));
    const isAllowed = (code) => {
      if (!allowedAliases.size) return true;
      return marketCodeAliases(code, marketSuffixes).some((alias) => allowedAliases.has(alias.toUpperCase()));
    };
    const setBase = (code, shares) => {
      const text = (code || '').toString().trim();
      const n = numericShares(shares);
      if (!text || n == null || n < 0 || !isAllowed(text)) return;
      baseHoldings[text] = n;
    };
    if (includeSnapshot) {
      snapshotEntries(snapshot).forEach(([sid, payload = {}]) => {
        const code = (payload?.ticker || sid || '').toString().trim();
        setBase(code, payload?.shares_held);
        if (code && sid && code !== sid) codeToStorageKey[code] = sid;
      });
    }
    (ops || []).forEach((op) => {
      const source = (op?._source || '').toString().trim();
      const decisionType = (op?.decision_type || '').toString().trim();
      if (source !== 'position_reconciliation' && decisionType !== 'position_reconciliation') return;
      const code = (op?.stock_id || '').toString().trim();
      const baselineShares = numericShares(op?.baseline_shares);
      if (!code || baselineShares == null) return;
      setBase(code, baselineShares);
      const storageKey = (op?.baseline_stock_id || '').toString().trim();
      if (storageKey && storageKey !== code) codeToStorageKey[code] = storageKey;
    });
    return baseHoldings;
  }

  function buildRebalancingApplyPayload({
    ops = [],
    stocks = [],
    edgeHoldings = [],
    snapshot = { stocks: {} },
    closedPositions = [],
    marketSuffixes = MARKET_SUFFIXES,
    weekId = '',
    dryRun = false,
  } = {}) {
    const stockNames = {};
    const codeToStorageKey = {};
    const displayCodes = [];
    const shownStocks = activeStocks({ stocks, snapshot, closedPositions, marketSuffixes });
    const shownEdges = activeEdgeHoldings({ edgeHoldings, closedPositions, marketSuffixes });

    shownStocks.forEach((stock) => {
      const code = stockDisplayCode(stock);
      const name = stock?.stock_name || stock?.stock_id || code;
      if (code) {
        stockNames[code] = name;
        if (stock?.ticker && stock?.stock_id && stock.ticker !== stock.stock_id) codeToStorageKey[code] = stock.stock_id;
        displayCodes.push(code);
      }
      if (stock?.stock_id) stockNames[stock.stock_id] = name;
    });
    shownEdges.forEach((edge) => {
      const code = edgeDisplayCode(edge);
      if (code) {
        stockNames[code] = (edge?.stock_name || '').trim() || code;
        displayCodes.push(code);
      }
    });

    return {
      week_id: weekId,
      stock_names: stockNames,
      ops,
      base_holdings: buildRebalancingBaseHoldings({ ops, snapshot, displayCodes, codeToStorageKey, marketSuffixes, includeSnapshot: false }),
      code_to_storage_key: codeToStorageKey,
      display_codes: displayCodes,
      dry_run: Boolean(dryRun),
    };
  }

  function opDelta(op) {
    const qty = numericShares(op?.quantity) || 0;
    const type = (op?.op_type || '').toString().trim().toLowerCase();
    if (BUY_TYPES.has(type)) return qty;
    if (SELL_TYPES.has(type)) return -qty;
    return 0;
  }

  function canonicalRows(snapshot, marketSuffixes = MARKET_SUFFIXES) {
    const rows = {};
    snapshotEntries(snapshot).forEach(([sid, payload = {}]) => {
      const code = payload?.ticker || sid;
      const primary = primaryMarketCode(code, marketSuffixes);
      rows[primary] = {
        stock_id: code,
        primary,
        shares: numericShares(payload?.shares_held) || 0,
      };
    });
    return rows;
  }

  function buildReconciliationReport({
    previousSnapshot = { stocks: {} },
    currentSnapshot = { stocks: {} },
    rebalancingOps = [],
    closedPositions = [],
    weekId = '',
    totalPortfolioValue = null,
    holdingValueHkd = null,
    cashBalanceHkd = null,
    marketSuffixes = MARKET_SUFFIXES,
  } = {}) {
    const previous = canonicalRows(previousSnapshot, marketSuffixes);
    const current = canonicalRows(currentSnapshot, marketSuffixes);
    const opByPrimary = {};
    (rebalancingOps || []).forEach((op) => {
      const primary = primaryMarketCode(op?.stock_id, marketSuffixes);
      if (!primary) return;
      opByPrimary[primary] = (opByPrimary[primary] || 0) + opDelta(op);
    });
    const closedAliases = new Set((closedPositions || []).flatMap((item) => marketCodeAliases(item?.stock_id, marketSuffixes).map((v) => v.toUpperCase())));
    const allPrimary = [...new Set([...Object.keys(previous), ...Object.keys(current), ...Object.keys(opByPrimary)])].sort();
    const positionChecks = allPrimary.map((primary) => {
      const before = previous[primary]?.shares || 0;
      const after = current[primary]?.shares || 0;
      const opDeltaValue = opByPrimary[primary] || 0;
      const expected = before + opDeltaValue;
      const delta = after - before;
      const mismatch = Math.abs(after - expected) > 1e-9;
      const stockId = current[primary]?.stock_id || previous[primary]?.stock_id || primary;
      const soldOut = before > 0 && after <= 0 && opDeltaValue < 0;
      const hasClosedPosition = marketCodeAliases(stockId, marketSuffixes).some((alias) => closedAliases.has(alias.toUpperCase()));
      return {
        stock_id: stockId,
        before,
        op_delta: opDeltaValue,
        expected,
        after,
        delta,
        mismatch,
        sold_out: soldOut,
        has_closed_position: !soldOut || hasClosedPosition,
      };
    });
    const missingClosed = positionChecks.filter((row) => row.sold_out && !row.has_closed_position);
    const mismatches = positionChecks.filter((row) => row.mismatch);
    const totalValue = numericShares(totalPortfolioValue);
    const holdingValue = numericShares(holdingValueHkd);
    const cashGap = totalValue != null && holdingValue != null ? Math.round((totalValue - holdingValue) * 100) / 100 : null;
    const cashBalance = numericShares(cashBalanceHkd);
    const cashBalanceGap = cashBalance != null && cashGap != null ? Math.round((cashBalance - cashGap) * 100) / 100 : null;
    const status = mismatches.length || missingClosed.length ? 'error' : 'healthy';
    return {
      week_id: weekId,
      status,
      summary: {
        position_mismatch_count: mismatches.length,
        missing_closed_position_count: missingClosed.length,
        portfolio_cash_gap_hkd: cashGap,
        cash_balance_hkd: cashBalance,
        cash_balance_gap_hkd: cashBalanceGap,
        checked_position_count: positionChecks.length,
      },
      position_checks: positionChecks,
      issues: [
        ...mismatches.map((row) => ({ key: 'position_delta_mismatch', stock_id: row.stock_id })),
        ...missingClosed.map((row) => ({ key: 'missing_closed_position', stock_id: row.stock_id })),
      ],
    };
  }

  window.weeklyReviewPortfolioHelpers = {
    MARKET_SUFFIXES,
    deepClone,
    numericShares,
    primaryMarketCode,
    marketCodeAliases,
    stockDisplayCode,
    edgeDisplayCode,
    snapshotStockForCode,
    previousSharesForCode,
    createPortfolioInteractionState,
    hasUnsavedShareEdits,
    resetEditDraftFromApplied,
    normalizeRebalancingOp,
    buildRebalancingDraftsFromShareEdits,
    buildRemoveHoldingDraft,
    activeStocks,
    activeEdgeHoldings,
    displayStocks,
    displayEdgeHoldings,
    buildRebalancingBaseHoldings,
    buildRebalancingApplyPayload,
    buildReconciliationReport,
  };
})();
