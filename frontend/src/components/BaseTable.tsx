import { useRef, useCallback, useMemo, useState, useEffect, type ReactNode } from 'react';
import {
  useReactTable,
  getCoreRowModel,
  getSortedRowModel,
  flexRender,
  type ColumnDef,
  type SortingState,
} from '@tanstack/react-table';
import SkeletonLoader from './SkeletonLoader';
import EmptyState from './EmptyState';
import styles from './BaseTable.module.css';

interface BaseTableProps<T extends Record<string, any>> {
  columns: ColumnDef<T, any>[];
  data: T[];
  loading?: boolean;
  emptyMessage?: string;
  dense?: boolean;
  sortable?: boolean;
  sortState?: SortingState;
  onSortChange?: (state: SortingState) => void;
  onRowClick?: (row: T) => void;
  renderRowDetail?: (row: T) => ReactNode;
  /** Return a label to show as a group divider between this row and the previous one, or null */
  groupLabel?: (row: T, index: number, all: T[]) => string | null;
  getRowId?: (row: T) => string;
  skeletonRows?: number;
  maxHeight?: string;
  /** Externally controlled expanded row id (overrides internal state) */
  expandedRow?: string | null;
  /** Called when row expand toggle happens externally */
  onExpandedChange?: (id: string | null) => void;
  /** Selected row ids (for checkbox/Space selection) */
  selectedRowIds?: Set<string>;
  /** Called when selection changes */
  onSelectionChange?: (ids: Set<string>) => void;
}

const DIVIDER_TYPE = Symbol('divider');

export default function BaseTable<T extends Record<string, any>>({
  columns, data, loading, emptyMessage = '暫無資料',
  dense, sortable = true, sortState: externalSort, onSortChange,
  onRowClick, renderRowDetail, groupLabel, getRowId,
  skeletonRows = 8, maxHeight,
  expandedRow: externalExpandedRow, onExpandedChange,
  selectedRowIds: externalSelectedRowIds, onSelectionChange,
}: BaseTableProps<T>) {
  const [internalSort, setInternalSort] = useState<SortingState>([]);
  const [expandedRows, setExpandedRows] = useState<Set<string>>(new Set());
  const [focusIndex, setFocusIndex] = useState(-1);
  const [internalSelectedRowIds, setInternalSelectedRowIds] = useState<Set<string>>(new Set());
  const tableRef = useRef<HTMLDivElement>(null);
  const lastClickedRef = useRef<number>(-1);

  const selectedRowIds = externalSelectedRowIds ?? internalSelectedRowIds;
  const toggleSelection = useCallback((id: string) => {
    const next = new Set(selectedRowIds);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    if (onSelectionChange) onSelectionChange(next);
    setInternalSelectedRowIds(next);
  }, [selectedRowIds, onSelectionChange]);

  const sorting = externalSort ?? internalSort;
  const setSorting = onSortChange ?? setInternalSort;

  const tableData = useMemo(() => {
    if (loading) return [];
    if (!groupLabel) return data;
    const result: (T | { _divider: string; _type: symbol })[] = [];
    for (let i = 0; i < data.length; i++) {
      const label = groupLabel(data[i], i, data);
      if (label && i > 0) {
        result.push({ _divider: label, _type: DIVIDER_TYPE });
      }
      result.push(data[i]);
    }
    return result;
  }, [data, groupLabel, loading]);

  const table = useReactTable({
    data: tableData as any[],
    columns,
    state: { sorting },
    onSortingChange: (updater: any) => {
      const next = typeof updater === 'function' ? updater(sorting) : updater;
      setSorting(next);
    },
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: sortable ? getSortedRowModel() : undefined,
    getRowId: getRowId as any,
    manualSorting: !!onSortChange,
  });

  const toggleExpand = useCallback((id: string) => {
    setExpandedRows((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const handleKeyDown = useCallback((e: React.KeyboardEvent, rowCount: number) => {
    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault();
        setFocusIndex((i) => {
          const next = Math.min(i + 1, rowCount - 1);
          if (e.shiftKey && lastClickedRef.current >= 0) {
            const start = Math.min(lastClickedRef.current, next);
            const end = Math.max(lastClickedRef.current, next);
            const ids = new Set<string>();
            for (let j = start; j <= end; j++) {
              const r = table.getRowModel().rows[j];
              if (r && (r.original as any)._type !== DIVIDER_TYPE) ids.add(r.id);
            }
            if (onSelectionChange) onSelectionChange(ids);
            setInternalSelectedRowIds(ids);
          }
          return next;
        });
        break;
      case 'ArrowUp':
        e.preventDefault();
        setFocusIndex((i) => {
          const next = Math.max(i - 1, 0);
          if (e.shiftKey && lastClickedRef.current >= 0) {
            const start = Math.min(lastClickedRef.current, next);
            const end = Math.max(lastClickedRef.current, next);
            const ids = new Set<string>();
            for (let j = start; j <= end; j++) {
              const r = table.getRowModel().rows[j];
              if (r && (r.original as any)._type !== DIVIDER_TYPE) ids.add(r.id);
            }
            if (onSelectionChange) onSelectionChange(ids);
            setInternalSelectedRowIds(ids);
          }
          return next;
        });
        break;
      case 'Home':
        e.preventDefault();
        setFocusIndex(0);
        break;
      case 'End':
        e.preventDefault();
        setFocusIndex(rowCount - 1);
        break;
      case 'Enter': {
        e.preventDefault();
        const row = table.getRowModel().rows[focusIndex];
        if (row && (row.original as any)._type !== DIVIDER_TYPE) {
          const id = row.id;
          if (onExpandedChange) {
            onExpandedChange(externalExpandedRow === id ? null : id);
          } else if (renderRowDetail) {
            toggleExpand(id);
          } else if (onRowClick) {
            onRowClick(row.original as T);
          }
        }
        break;
      }
      case ' ':
        e.preventDefault();
        const spaceRow = table.getRowModel().rows[focusIndex];
        if (spaceRow && (spaceRow.original as any)._type !== DIVIDER_TYPE) {
          toggleSelection(spaceRow.id);
          lastClickedRef.current = focusIndex;
        }
        break;
      case 'Escape':
        e.preventDefault();
        if (onExpandedChange) onExpandedChange(null);
        setExpandedRows(new Set());
        if (onSelectionChange) onSelectionChange(new Set());
        setInternalSelectedRowIds(new Set());
        break;
    }
  }, [focusIndex, renderRowDetail, onRowClick, toggleExpand, table, toggleSelection, onSelectionChange, externalExpandedRow, onExpandedChange]);

  useEffect(() => {
    if (focusIndex < 0 || !tableRef.current) return;
    const rows = tableRef.current.querySelectorAll('tr[role="row"]');
    const target = rows[focusIndex];
    if (target) {
      target.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
  }, [focusIndex]);

  if (loading) {
    return (
      <div className={styles.wrapper} ref={tableRef} tabIndex={0} style={maxHeight ? { maxHeight, overflowY: 'auto' } : undefined}>
        <table className={`${styles.table} ${dense ? styles.dense : ''}`} role="grid">
          <thead>
            <tr>{columns.map((col, i) => (
              <th key={i} style={{ width: (col as any).meta?.width } as React.CSSProperties}>
                {flexRender(col.header, {} as any)}
              </th>
            ))}</tr>
          </thead>
        </table>
        <SkeletonLoader variant="table" rows={skeletonRows} />
      </div>
    );
  }

  if (!loading && data.length === 0) {
    return (
      <div className={styles.wrapper}>
        <table className={`${styles.table} ${dense ? styles.dense : ''}`}>
          <thead>
            <tr>{columns.map((col, i) => (
              <th key={i} style={{ width: (col as any).meta?.width } as React.CSSProperties}>
                {flexRender(col.header, {} as any)}
              </th>
            ))}</tr>
          </thead>
        </table>
        <EmptyState message={emptyMessage} />
      </div>
    );
  }

  const rows = table.getRowModel().rows;

  return (
    <div
      className={styles.wrapper}
      ref={tableRef}
      tabIndex={0}
      role="grid"
      aria-label="data table"
      onKeyDown={(e) => handleKeyDown(e, rows.length)}
      style={maxHeight ? { maxHeight, overflowY: 'auto' } : undefined}
    >
      <table className={`${styles.table} ${dense ? styles.dense : ''}`}>
        <thead>
          {table.getHeaderGroups().map((hg) => (
            <tr key={hg.id}>
              {hg.headers.map((header) => {
                const sortDir = header.column.getIsSorted();
                const meta = header.column.columnDef.meta as any;
                return (
                  <th
                    key={header.id}
                    style={{ width: meta?.width, cursor: sortable ? 'pointer' : undefined } as React.CSSProperties}
                    onClick={sortable ? header.column.getToggleSortingHandler() : undefined}
                    aria-sort={sortDir === 'asc' ? 'ascending' : sortDir === 'desc' ? 'descending' : undefined}
                    data-type={meta?.align === 'right' ? 'number' : undefined}
                  >
                    {flexRender(header.column.columnDef.header, header.getContext())}
                    {sortable && sortDir === 'asc' && <span className={styles.sortIcon}> ▲</span>}
                    {sortable && sortDir === 'desc' && <span className={styles.sortIcon}> ▼</span>}
                    {sortable && !sortDir && <span className={styles.sortIconMuted}> ──</span>}
                  </th>
                );
              })}
            </tr>
          ))}
        </thead>
        <tbody>
          {rows.map((row, idx) => {
            const raw = row.original as any;
            if (raw._type === DIVIDER_TYPE) {
              return (
                <tr key={`div-${idx}`} className={styles.groupDividerRow}>
                  <td colSpan={columns.length} className={styles.groupDivider}>
                    — {raw._divider} —
                  </td>
                </tr>
              );
            }
            const isExpanded = onExpandedChange ? externalExpandedRow === row.id : expandedRows.has(row.id);
            const isFocused = idx === focusIndex;
            return (
              <tr key={row.id} role="row" aria-expanded={isExpanded}>
                <td colSpan={columns.length} style={{ padding: 0 }}>
                  <table className={styles.innerTable}>
                    <tbody>
                      <tr
                        className={`${styles.dataRow} ${dense ? styles.denseRow : ''} ${isFocused ? styles.focused : ''} ${selectedRowIds.has(row.id) ? styles.selected : ''}`}
                        tabIndex={-1}
                        onClick={() => {
                          if (onExpandedChange) {
                            onExpandedChange(isExpanded ? null : row.id);
                          } else if (renderRowDetail) {
                            toggleExpand(row.id);
                          } else {
                            onRowClick?.(raw as T);
                          }
                        }}
                        onMouseEnter={() => setFocusIndex(idx)}
                        onFocus={() => setFocusIndex(idx)}
                      >
                        {row.getVisibleCells().map((cell) => {
                          const meta = cell.column.columnDef.meta as any;
                          return (
                            <td
                              key={cell.id}
                              style={{ width: meta?.width } as React.CSSProperties}
                              data-type={meta?.align === 'right' ? 'number' : undefined}
                            >
                              {flexRender(cell.column.columnDef.cell, cell.getContext())}
                            </td>
                          );
                        })}
                      </tr>
                      {renderRowDetail && (
                        <tr className={styles.expandedRow}>
                          <td colSpan={row.getVisibleCells().length}>
                            <div className={`expand-collapse ${isExpanded ? 'open' : ''}`}>
                              <div className={styles.inlineDetail}>
                                {renderRowDetail(raw as T)}
                              </div>
                            </div>
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
