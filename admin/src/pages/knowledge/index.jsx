import { useState, useEffect, useCallback, useRef } from 'react';
import {
  Table, Tag, Button, Input, Modal, Form, Upload,
  message, Progress, Result, Select, Divider, Drawer, Space, Badge, Popconfirm,
} from 'antd';
import {
  UploadOutlined,
  InboxOutlined,
  SettingOutlined,
  DeleteOutlined,
  HistoryOutlined,
  ReloadOutlined,
} from '@ant-design/icons';
import {
  getCollectionFiles,
  uploadKnowledgeBatch,
  getUploadBatches,
  getUploadBatchStatus,
  retryUploadBatch,
  deleteUploadBatch,
  deleteFile,
} from '../../api/admin';
import { clearPageCacheByPrefix, readPageCache, writePageCache } from '../../utils/pageCache';
import { getUserRole } from '../../utils/auth';

// ── Knowledge directory tree ─────────────────────────────────────────

const makeCategories = (scopeKey, accessLevel) => [
  {
    key: `${scopeKey}_documents`, name: 'Documents', icon: '📄', type: 'category',
    collection: 'documents',
    children: [
      { key: `${scopeKey}_documents_default`, name: 'Default Category', icon: '📂', type: 'category', collection: 'documents', sub_category: 'Default Category', access_level: accessLevel, children: [] },
    ]
  },
  {
    key: `${scopeKey}_manuals`, name: 'Manuals', icon: '📘', type: 'category',
    collection: 'manuals',
    children: [
      { key: `${scopeKey}_manuals_default`, name: 'Default Category', icon: '📂', type: 'category', collection: 'manuals', sub_category: 'Default Category', access_level: accessLevel, children: [] },
    ]
  },
];

const KNOWLEDGE_TREE = [
  { key: 'internal', name: 'Internal', icon: '🔒', type: 'scope', children: makeCategories('internal', 'internal') },
  { key: 'internal_agent', name: 'Agent', icon: '🔑', type: 'scope', children: makeCategories('agent', 'internal_agent') },
  { key: 'public', name: 'Public', icon: '🌐', type: 'scope', children: makeCategories('public', 'public') },
];

const TYPE_BADGE = { PDF: 'blue', Word: 'purple', Excel: 'orange', Video: 'default' };

// ── Upload form constants ─────────────────────────────────────────────

const ACCESS_LEVEL_OPTIONS = [
  { value: 'internal',       label: 'Internal' },
  { value: 'internal_agent', label: 'Agent' },
  { value: 'public',         label: 'Public' },
];

const COLLECTION_OPTIONS = [
  { value: 'documents', label: 'Documents' },
  { value: 'manuals',   label: 'Manuals' },
];

const SUB_CATEGORY_MAP = {
  documents: ['Default Category'],
  manuals:   ['Default Category'],
};

const DEFAULT_UPLOAD_METADATA_FIELDS = [
  { key: 'name',        label: 'Name',        input_type: 'text', options: [], placeholder: '' },
  { key: 'tags',        label: 'Tags',        input_type: 'text', options: [], placeholder: '' },
  { key: 'description', label: 'Description', input_type: 'text', options: [], placeholder: '' },
];
const TEST_UPLOAD_METADATA_FIELD_NAMES = new Set(['abc', 'zxcv']);
const FIELD_TYPE_LABEL_MAP = {
  text: '文字输入',
  select: '下拉框',
};
const KNOWN_UPLOAD_OPTION_FIELDS = new Set(['model', 'series', 'generation', 'controller', 'product_line', 'tonnage']);

const toOpts = (arr) => arr.map(v => ({ value: v, label: v }));
const selectFilterOption = (input, option) => {
  const keyword = String(input || '').trim().toLowerCase();
  const label = String(option?.label ?? option?.value ?? '').toLowerCase();
  return label.includes(keyword);
};
const SEARCHABLE_SELECT_PROPS = {
  showSearch: true,
  optionFilterProp: 'label',
  filterOption: selectFilterOption,
};
const ACCESS_LEVEL_TO_PERMISSION = {
  internal: '1',
  internal_agent: '2',
  public: '3',
};
const PERMISSION_LABEL_MAP = {
  '1': 'Internal',
  '2': 'Agent',
  '3': 'Public',
  internal: 'Internal',
  internal_agent: 'Agent',
  public: 'Public',
};
const formatPermissionLabel = (value) => PERMISSION_LABEL_MAP[value] || value || '—';
const COLLECTION_ROUTE_MAP = {
  documents: { uploadCollectionType: 'documents' },
  manuals:   { uploadCollectionType: 'manuals' },
};
const KNOWLEDGE_COUNTS_CACHE_KEY = 'knowledge:nodeCounts:v2';
const KNOWLEDGE_COUNTS_CACHE_TTL = 60 * 1000;
const KNOWLEDGE_LIST_CACHE_TTL = 15 * 1000;
const ACTIVE_UPLOAD_STATUSES = new Set(['running', 'pending']);
const FAILED_UPLOAD_STATUSES = new Set(['failed', 'partial_failed']);
const shouldCleanupUploadRecords = (status) => status !== 'done';
const getUploadDeleteDescription = (status) => {
  if (!shouldCleanupUploadRecords(status)) {
    return '仅从任务列表删除该记录，不会删除已入库知识，确定删除？';
  }
  if (ACTIVE_UPLOAD_STATUSES.has(status)) {
    return '将停止后台任务，并尝试清理临时文件及该批次已入库记录，确定删除？';
  }
  return '将尝试清理临时文件及该批次已入库记录，确定删除？';
};
const getUploadDeleteSuccessMessage = (status) => {
  if (!shouldCleanupUploadRecords(status)) return '已删除上传任务';
  if (ACTIVE_UPLOAD_STATUSES.has(status)) return '已停止并删除上传任务，已执行入库记录清理';
  return '已删除上传任务，已执行入库记录清理';
};
const UPLOAD_STATUS_COLOR = {
  running: 'processing',
  pending: 'default',
  done: 'success',
  failed: 'error',
  partial_failed: 'warning',
  unknown: 'default',
};

/** 从 selectedNode 推导上传表单初始值（access_level / collection / sub_category） */
const getUploadInitValues = (node) => {
  let access_level = '', collection = '', sub_category = '';
  if (!node) return { access_level, collection, sub_category };

  if (node.access_level) {
    access_level = node.access_level;
  } else if (node.type === 'scope') {
    access_level = node.key; // 'public' / 'internal' / 'internal_agent'
  }

  if (node.collection && node.type !== 'scope') {
    collection = node.collection;
  }

  if (node.sub_category) {
    sub_category = node.sub_category;
  }

  return { access_level, collection, sub_category };
};

const cloneMetadataFields = (fields) => (
  (fields || []).map((field) => ({
    key: String(field.key || ''),
    label: String(field.label || ''),
    input_type: field.input_type === 'select' ? 'select' : 'text',
    options: Array.isArray(field.options) ? field.options.map(String) : [],
    placeholder: String(field.placeholder || ''),
  }))
);

const isTestMetadataField = (field) => {
  const key = String(field?.key || '').trim().toLowerCase();
  const label = String(field?.label || '').trim().toLowerCase();
  return TEST_UPLOAD_METADATA_FIELD_NAMES.has(key) || TEST_UPLOAD_METADATA_FIELD_NAMES.has(label);
};

const normalizeMetadataFields = (fields) => {
  if (!Array.isArray(fields)) return cloneMetadataFields(DEFAULT_UPLOAD_METADATA_FIELDS);
  const next = cloneMetadataFields(fields)
    .filter((field) => !isTestMetadataField(field))
    .map((field) => ({
      ...field,
      key: field.key.trim(),
      label: field.label.trim(),
      placeholder: field.placeholder.trim(),
      options: field.input_type === 'select'
        ? Array.from(new Set(field.options.map(item => String(item).trim()).filter(Boolean)))
        : [],
    }))
    .filter(field => field.key && field.label);
  return next;
};

const splitUploadMetadataValues = (values, fields) => {
  const optionValues = {};
  const metadataValues = {};
  normalizeMetadataFields(fields).forEach((field) => {
    const rawValue = values[field.key];
    if (rawValue === undefined || rawValue === null || rawValue === '') return;
    const value = typeof rawValue === 'string' ? rawValue.trim() : rawValue;
    if (value === '') return;
    if (KNOWN_UPLOAD_OPTION_FIELDS.has(field.key)) {
      optionValues[field.key] = value;
    } else {
      metadataValues[field.key] = value;
    }
  });
  return { optionValues, metadataValues };
};

const normalizeBatchProgress = (batch) => {
  const status = batch?.status || 'unknown';
  const isCompleted = status === 'done';
  const isFailed = FAILED_UPLOAD_STATUSES.has(status) || status === 'unknown';
  return {
    mode: 'batch',
    phase: 'ingest',
    status: isCompleted ? 'completed' : (isFailed ? 'failed' : 'running'),
    batchStatus: status,
    displayStatus: batch?.display_status || '',
    progress: (() => {
      const sc = Number(batch?.active_step_current);
      const st = Number(batch?.active_step_total);
      if (sc > 0 && st > 0) {
        return Math.min(100, Math.round((sc / st) * 100));
      }
      return Number(batch?.progress_percent) || 0;
    })(),
    uploadProgress: 100,
    message: batch?.message || batch?.active_progress_detail || batch?.active_step_name || '正在处理入库任务',
    batchId: batch?.batch_id,
    items: batch?.items || [],
    total: batch?.total || 0,
    done: batch?.done || 0,
    failed: batch?.failed || 0,
    activeStepName: batch?.active_step_name || '',
    activeProgressDetail: batch?.active_progress_detail || '',
    activeStepCurrent: Number(batch?.active_step_current) || null,
    activeStepTotal: Number(batch?.active_step_total) || null,
    collectionName: batch?.collection_name || '',
    collectionType: batch?.collection_type || '',
  };
};

const normalizeUploadTasksPayload = (payload) => {
  if (Array.isArray(payload)) return payload;
  if (Array.isArray(payload?.items)) return payload.items;
  return [];
};

// ── 辅助：递归查找节点及其祖先路径 ─────────────────────────────────

function findNodePath(tree, targetKey, path = []) {
  for (const node of tree) {
    const currentPath = [...path, node];
    if (node.key === targetKey) return currentPath;
    if (node.children) {
      const found = findNodePath(node.children, targetKey, currentPath);
      if (found) return found;
    }
  }
  return null;
}

const getNodeAccessLevel = (node, inheritedAccessLevel = '') => {
  if (!node) return inheritedAccessLevel || '';
  if (node.access_level) return node.access_level;
  if (node.type === 'scope' && ACCESS_LEVEL_TO_PERMISSION[node.key]) return node.key;
  if (inheritedAccessLevel) return inheritedAccessLevel;
  const path = findNodePath(KNOWLEDGE_TREE, node.key) || [];
  const scopeNode = path.find(item => item.type === 'scope' && ACCESS_LEVEL_TO_PERMISSION[item.key]);
  return scopeNode?.key || '';
};

// ── 自定义树节点组件 ─────────────────────────────────────────────────

function TreeNode({ name, icon, count, depth, selected, hasChildren, open, onToggle, onSelect }) {
  return (
    <div
      onClick={onSelect}
      style={{
        display: 'flex', alignItems: 'center', gap: 4,
        paddingLeft: 12 + depth * 20, paddingRight: 12,
        paddingTop: 5, paddingBottom: 5,
        cursor: 'pointer', fontSize: 13, color: selected ? '#1d4ed8' : '#334155',
        background: selected ? '#dbeafe' : 'transparent',
        fontWeight: selected ? 500 : 400,
        transition: 'background .15s', whiteSpace: 'nowrap', userSelect: 'none',
      }}
      onMouseEnter={e => { if (!selected) e.currentTarget.style.background = '#eff6ff'; }}
      onMouseLeave={e => { if (!selected) e.currentTarget.style.background = 'transparent'; }}
    >
      <span
        onClick={e => { e.stopPropagation(); onToggle?.(); }}
        style={{
          width: 18, height: 18, display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontSize: 10, color: '#94a3b8', flexShrink: 0, cursor: 'pointer',
          transition: 'transform .2s', transform: open ? 'rotate(90deg)' : 'rotate(0deg)',
          visibility: hasChildren ? 'visible' : 'hidden',
        }}
      >▶</span>
      <span style={{ fontSize: 15, flexShrink: 0 }}>{icon}</span>
      <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis' }}>{name}</span>
      {count !== undefined && (
        <span style={{ fontSize: 11, color: '#94a3b8', marginLeft: 4 }}>({count})</span>
      )}
    </div>
  );
}

// ── 主组件 ──────────────────────────────────────────────────────────

export default function Knowledge() {
  const [nodeCounts, setNodeCounts] = useState({});
  const [selectedKey, setSelectedKey] = useState('__root__');
  const [selectedNode, setSelectedNode] = useState(null); // 当前选中的树节点对象
  const [breadcrumb, setBreadcrumb] = useState([{ label: '📚 全部知识库', key: '__root__' }]);
  const [showFolderCards, setShowFolderCards] = useState('root'); // 'root' | 'scope' | 'category' | null

  const [expanded, setExpanded] = useState({ __root__: true });

  const [files, setFiles] = useState([]);
  const [fileTotal, setFileTotal] = useState(0);
  const [filePage, setFilePage] = useState(1);
  const [fileLoading, setFileLoading] = useState(false);
  const [searchText, setSearchText] = useState('');

  const [uploadOpen, setUploadOpen] = useState(false);
  const [uploadForm] = Form.useForm();
  const watchedCollection = Form.useWatch('collection', uploadForm);
  const uploadInitValues = getUploadInitValues(selectedNode);
  const isAdminAccount = getUserRole() === 'admin';

  const [uploadMetadataFields] = useState(() => cloneMetadataFields(DEFAULT_UPLOAD_METADATA_FIELDS));

  // ── 入库进度轮询 ───────────────────────────────────────────────────
  const [ingestProgress, setIngestProgress] = useState(null);
  const [showProgressModal, setShowProgressModal] = useState(false);
  const [uploadTasks, setUploadTasks] = useState([]);
  const [uploadTaskDrawerOpen, setUploadTaskDrawerOpen] = useState(false);
  const [uploadTasksLoading, setUploadTasksLoading] = useState(false);
  const progressTimerRef = useRef(null);
  const recoveredBatchRef = useRef(new Set());

  const buildKnowledgeListCacheKey = useCallback((node, page, keyword) => JSON.stringify({
    selectedKey: node?.key || '__root__',
    collection: node?.collection || '',
    category: node?.sub_category || '',
    page,
    keyword: keyword || '',
  }), []);

  const clearKnowledgePageCache = useCallback(() => {
    clearPageCacheByPrefix('knowledge:');
    clearPageCacheByPrefix('dashboard:');
  }, []);

  const stopProgressPolling = useCallback(() => {
    if (progressTimerRef.current) {
      clearInterval(progressTimerRef.current);
      progressTimerRef.current = null;
    }
  }, []);

  useEffect(() => () => { stopProgressPolling(); }, [stopProgressPolling]);

  const loadNodeCounts = useCallback(async ({ force = false } = {}) => {
    const cachedCounts = readPageCache(KNOWLEDGE_COUNTS_CACHE_KEY, KNOWLEDGE_COUNTS_CACHE_TTL);
    if (cachedCounts) {
      setNodeCounts(cachedCounts);
      if (!force) {
        return;
      }
    }
    const requestCache = new Map();

    const buildRequestKey = (node, effectiveAccessLevel) => JSON.stringify({
      collection: node.collection || '',
      category: node.sub_category || '',
      permissionLevel: ACCESS_LEVEL_TO_PERMISSION[effectiveAccessLevel] || '',
    });

    const ensureCountRequest = (node, effectiveAccessLevel) => {
      const key = buildRequestKey(node, effectiveAccessLevel);
      if (!requestCache.has(key)) {
        const params = { page: 1, page_size: 1 };
        if (node.sub_category) params.category = node.sub_category;
        const permissionLevel = ACCESS_LEVEL_TO_PERMISSION[effectiveAccessLevel];
        if (permissionLevel) params.permission_level = permissionLevel;

        const request = getCollectionFiles(node.collection, params).then((res) => {
          const data = res.data || res;
          return data.total || 0;
        });

        requestCache.set(key, request.catch(() => 0));
      }
      return key;
    };

    const preloadLeafRequests = (nodes, inheritedAccessLevel = '') => {
      nodes.forEach((node) => {
        const effectiveAccessLevel = getNodeAccessLevel(node, inheritedAccessLevel);
        if (node.children?.length) {
          preloadLeafRequests(node.children, effectiveAccessLevel);
          return;
        }
        if (node.collection) {
          ensureCountRequest(node, effectiveAccessLevel);
        }
      });
    };

    preloadLeafRequests(KNOWLEDGE_TREE);
    const resolvedCounts = new Map(
      await Promise.all(
        Array.from(requestCache.entries()).map(async ([key, promise]) => [key, await promise]),
      ),
    );

    const nextCounts = {};
    const fillNodeCounts = (nodes, inheritedAccessLevel = '') => {
      nodes.forEach((node) => {
        const effectiveAccessLevel = getNodeAccessLevel(node, inheritedAccessLevel);
        if (node.children?.length) {
          fillNodeCounts(node.children, effectiveAccessLevel);
          nextCounts[node.key] = node.children.reduce((sum, child) => sum + (nextCounts[child.key] || 0), 0);
          return;
        }
        if (node.collection) {
          nextCounts[node.key] = resolvedCounts.get(buildRequestKey(node, effectiveAccessLevel)) || 0;
        }
      });
    };

    fillNodeCounts(KNOWLEDGE_TREE);
    setNodeCounts(nextCounts);
    writePageCache(KNOWLEDGE_COUNTS_CACHE_KEY, nextCounts);
  }, []);

  useEffect(() => {
    loadNodeCounts();
  }, [loadNodeCounts]);

  const getNodeCount = (node) => {
    if (node.type === 'scope') return undefined;
    return nodeCounts[node.key];
  };

  useEffect(() => {
    if (!uploadOpen) return;
    const nextInitValues = getUploadInitValues(selectedNode);
    uploadForm.resetFields();
    uploadForm.setFieldsValue(nextInitValues);
  }, [uploadOpen, uploadForm, selectedNode]);

  const loadFiles = useCallback(async (collName, page, keyword, nodeOverride = null) => {
    if (!collName) return;
    const currentNode = nodeOverride || selectedNode;
    const cacheKey = `knowledge:list:${buildKnowledgeListCacheKey(currentNode, page, keyword)}`;
    const cachedList = readPageCache(cacheKey, KNOWLEDGE_LIST_CACHE_TTL);
    if (cachedList) {
      setFiles(cachedList.files || []);
      setFileTotal(cachedList.fileTotal || 0);
      setFileLoading(false);
    } else {
      setFileLoading(true);
    }
    try {
      const params = { page, page_size: 20 };
      if (keyword) params.keyword = keyword;
      if (currentNode?.sub_category) params.category = currentNode.sub_category;
      const permissionLevel = ACCESS_LEVEL_TO_PERMISSION[getNodeAccessLevel(currentNode)];
      if (permissionLevel) params.permission_level = permissionLevel;

      const res = await getCollectionFiles(collName, params);
      const d = res.data || res;
      setFiles(d.files || d.items || []);
      setFileTotal(d.total || 0);
      writePageCache(cacheKey, {
        files: d.files || d.items || [],
        fileTotal: d.total || 0,
      });
    } catch { /* handled */ }
    finally { setFileLoading(false); }
  }, [buildKnowledgeListCacheKey, selectedNode]);

  const startBatchPolling = useCallback((batchId, collName, { openModal = true } = {}) => {
    stopProgressPolling();
    if (openModal) setShowProgressModal(true);
    setIngestProgress({
      mode: 'batch',
      phase: 'ingest',
      status: 'pending',
      batchStatus: 'pending',
      progress: 0,
      uploadProgress: 100,
      message: '任务已提交，等待入库开始...',
      batchId,
      items: [],
    });

    const poll = async () => {
      try {
        const res = await getUploadBatchStatus(batchId);
        const data = res.data || res;
        const nextProgress = normalizeBatchProgress(data);
        setIngestProgress(nextProgress);
        setUploadTasks((prev) => {
          const nextTasks = prev.filter((task) => task.batch_id !== data.batch_id);
          return [data, ...nextTasks];
        });

        if (nextProgress.status === 'completed' || nextProgress.status === 'failed') {
          stopProgressPolling();
          if (nextProgress.status === 'completed') {
            message.success('上传入库完成');
          } else {
            message.warning('上传已结束，存在失败文件');
          }
          clearKnowledgePageCache();
          loadFiles(collName, 1, '', selectedNode);
          loadNodeCounts({ force: true });
        }
      } catch {
        stopProgressPolling();
      }
    };

    poll();
    progressTimerRef.current = setInterval(poll, 2000);
  }, [clearKnowledgePageCache, loadFiles, loadNodeCounts, stopProgressPolling, selectedNode]);

  const loadUploadTasks = useCallback(async ({ autoRecover = false } = {}) => {
    setUploadTasksLoading(true);
    try {
      const res = await getUploadBatches();
      const data = res.data || res;
      const tasks = normalizeUploadTasksPayload(data);
      setUploadTasks(tasks);

      if (autoRecover) {
        const runningTask = tasks.find((task) => ACTIVE_UPLOAD_STATUSES.has(task.status));
        if (runningTask?.batch_id && !recoveredBatchRef.current.has(runningTask.batch_id)) {
          recoveredBatchRef.current.add(runningTask.batch_id);
          startBatchPolling(
            runningTask.batch_id,
            runningTask.collection_name || runningTask.collection_type || selectedNode?.collection || '',
          );
        }
      }
      return tasks;
    } finally {
      setUploadTasksLoading(false);
    }
  }, [selectedNode, startBatchPolling]);

  const activeUploadCount = uploadTasks.filter((task) => ACTIVE_UPLOAD_STATUSES.has(task.status)).length;

  useEffect(() => {
    loadUploadTasks({ autoRecover: true });
  }, [loadUploadTasks]);

  useEffect(() => {
    const shouldPoll = uploadTaskDrawerOpen || activeUploadCount > 0;
    if (!shouldPoll) return;
    const timer = setInterval(() => {
      loadUploadTasks({ autoRecover: false });
    }, 15000);
    return () => clearInterval(timer);
  }, [activeUploadCount, loadUploadTasks, uploadTaskDrawerOpen]);

  const retryBatch = useCallback(async (batchId, collName = '') => {
    if (!batchId) return;
    try {
      await retryUploadBatch(batchId, {});
      message.success('已重新提交失败项');
      startBatchPolling(batchId, collName || selectedNode?.collection || '');
      loadUploadTasks({ autoRecover: false });
    } catch (e) {
      message.error('重试失败：' + (e?.response?.data?.detail || e?.message || '未知错误'));
    }
  }, [loadUploadTasks, selectedNode, startBatchPolling]);

  const deleteBatch = useCallback(async (record) => {
    if (!record?.batch_id) return;
    try {
      const res = await deleteUploadBatch(record.batch_id);
      const responseData = res?.data?.data || res?.data || {};
      recoveredBatchRef.current.delete(record.batch_id);
      setUploadTasks((prev) => prev.filter((task) => task.batch_id !== record.batch_id));
      if (ingestProgress?.batchId === record.batch_id) {
        stopProgressPolling();
        setShowProgressModal(false);
        setIngestProgress(null);
      }
      const remainingMilvusVectors = Number(responseData.remaining_milvus_vectors) || 0;
      if (remainingMilvusVectors > 0) {
        message.warning(`已删除上传任务，但仍有 ${remainingMilvusVectors} 条向量残留，后台已记录日志`);
      } else {
        message.success(getUploadDeleteSuccessMessage(record.status));
      }
      clearKnowledgePageCache();
      if (selectedNode?.collection) {
        loadFiles(selectedNode.collection, filePage, searchText, selectedNode);
      }
      loadNodeCounts({ force: true });
      loadUploadTasks({ autoRecover: false });
    } catch (e) {
      message.error('删除上传任务失败：' + (e?.response?.data?.detail || e?.message || '未知错误'));
    }
  }, [
    clearKnowledgePageCache,
    filePage,
    ingestProgress?.batchId,
    loadFiles,
    loadNodeCounts,
    loadUploadTasks,
    searchText,
    selectedNode,
    stopProgressPolling,
  ]);

  const handleDelete = useCallback(async (record) => {
    try {
      await deleteFile(record.document_id || record.file_id, record.collection_name);
      message.success('文件已删除');
      clearKnowledgePageCache();
      if (selectedNode?.collection) {
        loadFiles(selectedNode.collection, filePage, searchText, selectedNode);
      }
      loadNodeCounts({ force: true });
    } catch {
      message.error('删除失败');
    }
  }, [clearKnowledgePageCache, filePage, loadFiles, loadNodeCounts, searchText, selectedNode]);

  const handleSearch = (value) => {
    setSearchText(value);
    if (selectedNode?.collection) {
      setFilePage(1);
      loadFiles(selectedNode.collection, 1, value, selectedNode);
    }
  };

  const toggleExpand = (key) => setExpanded(prev => ({ ...prev, [key]: !prev[key] }));

  // ── 导航 ─────────────────────────────────────────────────

  const navigateTo = useCallback((key, node) => {
    setSelectedKey(key);
    setSelectedNode(node);
    setSearchText('');

    if (key === '__root__') {
      setBreadcrumb([{ label: '📚 全部知识库', key: '__root__' }]);
      setShowFolderCards('root');
      setFiles([]);
      return;
    }

    // 构建面包屑
    const path = findNodePath(KNOWLEDGE_TREE, key);
    if (!path) return;
    const crumbs = [{ label: '📚 全部知识库', key: '__root__' }];
    for (const p of path) {
      crumbs.push({ label: `${p.icon} ${p.name}`, key: p.key });
    }
    setBreadcrumb(crumbs);

    const target = path[path.length - 1];

    if (target.type === 'scope') {
      setShowFolderCards('scope');
      setFiles([]);
    } else if (target.type === 'category') {
      // category 层：如果有子节点显示子文件夹卡片，否则加载文件
      if (target.children && target.children.length > 0) {
        setShowFolderCards('category');
        setFiles([]);
      } else if (target.collection) {
        setShowFolderCards(null);
        setFilePage(1);
        loadFiles(target.collection, 1, '', target);
      }
    } else if (target.type === 'sub') {
      setShowFolderCards(null);
      setFilePage(1);
      loadFiles(target.collection, 1, '', target);
    }
  }, [loadFiles]);

  // ── 表格列（file_registry 字段） ──────────────────────────


  const columns = [
    {
      title: '文件名', dataIndex: 'filename', key: 'filename', ellipsis: true,
      render: (text, record) => (
        <span>{record.file_type === 'pdf' ? '📄' : '📝'} {text}</span>
      ),
    },
    {
      title: '类型', dataIndex: 'file_type', key: 'file_type', width: 90,
      render: (text) => <Tag color={text === 'pdf' ? 'blue' : text === 'docx' || text === 'doc' ? 'purple' : 'default'}>{(text || '-').toUpperCase()}</Tag>,
    },
    {
      title: '目录', dataIndex: 'folder_path_label', key: 'folder_path_label', width: 160,
      render: (text) => text || '—',
    },
    {
      title: '上传时间', dataIndex: 'uploaded_at', key: 'uploaded_at', width: 170,
      render: (text) => text ? new Date(text).toLocaleString('zh-CN') : '-',
    },
    {
      title: '操作', key: 'action', width: 120,
      render: (_, record) => (
        <Popconfirm
          title="确认删除这条知识？"
          description="删除后该知识将不再出现在当前知识库列表中。"
          okText="确认删除"
          cancelText="取消"
          okButtonProps={{ danger: true }}
          onConfirm={() => handleDelete(record)}
        >
          <Button danger size="small">删除</Button>
        </Popconfirm>
      ),
    },
  ];

  // ── 渲染树 ──────────────────────────────────────────────

  const renderTreeNodes = (nodes, depth) => {
    return nodes.map((node, idx) => {
      const hasChildren = node.children && node.children.length > 0;
      const isOpen = expanded[node.key];
      const count = getNodeCount(node);
      // scope 节点始终显示展开箭头（即使 children 为空），避免和叶子节点混淆
      const showArrow = node.type === 'scope' || hasChildren;

      return (
        <div key={node.key}>
          {/* scope 节点之间加分隔线，与上方 children 区分层级 */}
          {node.type === 'scope' && idx > 0 && (
            <div style={{ height: 1, background: '#e2e8f0', margin: '4px 12px' }} />
          )}
          <TreeNode
            name={node.name} icon={node.icon} count={count}
            depth={depth} selected={selectedKey === node.key}
            hasChildren={showArrow} open={isOpen}
            onToggle={() => toggleExpand(node.key)}
            onSelect={() => {
              if (hasChildren) toggleExpand(node.key);
              navigateTo(node.key, node);
            }}
          />
          {isOpen && hasChildren && renderTreeNodes(node.children, depth + 1)}
        </div>
      );
    });
  };

  const renderTree = () => {
    return (
      <>
        <TreeNode name="全部知识库" icon="📚" depth={0}
          selected={selectedKey === '__root__'} hasChildren open={expanded.__root__}
          onToggle={() => toggleExpand('__root__')}
          onSelect={() => navigateTo('__root__', null)} />
        {expanded.__root__ && renderTreeNodes(KNOWLEDGE_TREE, 1)}
      </>
    );
  };

  // ── 渲染文件夹卡片 ─────────────────────────────────────

  const renderFolderCards = () => {
    let items = [];

    if (showFolderCards === 'root') {
      items = KNOWLEDGE_TREE;
    } else if (showFolderCards === 'scope') {
      const path = findNodePath(KNOWLEDGE_TREE, selectedKey);
      const scopeNode = path ? path[path.length - 1] : null;
      items = scopeNode?.children || [];
    } else if (showFolderCards === 'category') {
      const path = findNodePath(KNOWLEDGE_TREE, selectedKey);
      const catNode = path ? path[path.length - 1] : null;
      items = catNode?.children || [];
    }

    if (items.length === 0) {
      return <div style={{ textAlign: 'center', color: '#94a3b8', padding: '60px 0' }}>暂无内容</div>;
    }

    return (
      <div style={{ padding: 24, display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))', gap: 14 }}>
        {items.map(item => {
          const count = getNodeCount(item);
          return (
            <div key={item.key}
              onClick={() => {
                toggleExpand(item.key);
                navigateTo(item.key, item);
              }}
              style={{
                border: '1px solid #e2e8f0', borderRadius: 8, padding: '20px 14px',
                textAlign: 'center', cursor: 'pointer', transition: '.15s', background: '#fff',
              }}
              onMouseEnter={e => { e.currentTarget.style.borderColor = '#60a5fa'; e.currentTarget.style.boxShadow = '0 2px 8px rgba(0,0,0,.06)'; }}
              onMouseLeave={e => { e.currentTarget.style.borderColor = '#e2e8f0'; e.currentTarget.style.boxShadow = 'none'; }}
            >
              <div style={{ fontSize: 36, marginBottom: 8 }}>{item.icon}</div>
              <div style={{ fontSize: 13, color: '#334155', fontWeight: 500 }}>{item.name}</div>
              {count !== undefined && (
                <div style={{ fontSize: 11, color: '#94a3b8', marginTop: 4 }}>{count} 个文档</div>
              )}
            </div>
          );
        })}
      </div>
    );
  };

  const uploadTaskColumns = [
    {
      title: '批次',
      dataIndex: 'batch_id',
      key: 'batch_id',
      width: 180,
      render: (value) => <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{value}</span>,
    },
    {
      title: '状态',
      dataIndex: 'display_status',
      key: 'display_status',
      width: 90,
      render: (value, record) => (
        <Tag color={UPLOAD_STATUS_COLOR[record.status] || 'default'}>{value || record.status}</Tag>
      ),
    },
    {
      title: '进度',
      dataIndex: 'progress_percent',
      key: 'progress_percent',
      width: 170,
      render: (value, record) => (
        <Progress
          percent={Number(value) || 0}
          size="small"
          status={FAILED_UPLOAD_STATUSES.has(record.status) ? 'exception' : (record.status === 'done' ? 'success' : 'active')}
        />
      ),
    },
    {
      title: '当前阶段',
      key: 'message',
      render: (_, record) => (
        <div style={{ minWidth: 180 }}>
          <div style={{ color: '#334155', fontSize: 12 }}>{record.active_step_name || record.display_status || '-'}</div>
          <div style={{ color: '#64748b', fontSize: 12, marginTop: 2 }}>{record.message || record.active_progress_detail || '-'}</div>
        </div>
      ),
    },
    {
      title: '操作',
      key: 'actions',
      width: 220,
      render: (_, record) => (
        <Space size={6}>
          <Button
            size="small"
            onClick={() => {
              startBatchPolling(record.batch_id, record.collection_name || record.collection_type || selectedNode?.collection || '');
              setShowProgressModal(true);
            }}
          >
            查看
          </Button>
          {FAILED_UPLOAD_STATUSES.has(record.status) && (
            <Button size="small" icon={<ReloadOutlined />} onClick={() => retryBatch(record.batch_id, record.collection_name || record.collection_type)}>
              重试
            </Button>
          )}
          <Popconfirm
            title="删除上传任务"
            description={getUploadDeleteDescription(record.status)}
            okText="删除"
            cancelText="取消"
            okButtonProps={{ danger: true }}
            onConfirm={() => deleteBatch(record)}
          >
            <Button size="small" danger icon={<DeleteOutlined />}>
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  // ── 主渲染 ──────────────────────────────────────────────

  return (
    <div style={{ display: 'flex', gap: 0, height: 'calc(100vh - 56px - 48px)', margin: '-24px -28px' }}>

      {/* 左侧：树形导航 */}
      <div style={{
        width: 280, flexShrink: 0, background: '#f8fafc',
        borderRight: '1px solid #e2e8f0', overflowY: 'auto', padding: '12px 0',
      }}>
        {renderTree()}
      </div>

      {/* 右侧：内容面板 */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>

        {/* 面包屑 */}
        <div style={{
          display: 'flex', alignItems: 'center', gap: 4,
          padding: '10px 20px', fontSize: 13, color: '#64748b',
          borderBottom: '1px solid #e2e8f0', background: '#fff', flexShrink: 0,
        }}>
          {breadcrumb.map((item, i) => (
            <span key={i}>
              {i > 0 && <span style={{ color: '#cbd5e1', margin: '0 2px' }}> / </span>}
              {i < breadcrumb.length - 1 ? (
                <span style={{ cursor: 'pointer', color: '#2563eb' }}
                  onClick={() => navigateTo(item.key, null)}
                  onMouseEnter={e => e.currentTarget.style.textDecoration = 'underline'}
                  onMouseLeave={e => e.currentTarget.style.textDecoration = 'none'}
                >{item.label}</span>
              ) : (
                <span style={{ color: '#334155', fontWeight: 500 }}>{item.label}</span>
              )}
            </span>
          ))}
        </div>

        {/* 工具栏 */}
        <div style={{
          display: 'flex', alignItems: 'center', gap: 12,
          padding: '10px 20px', borderBottom: '1px solid #f1f5f9',
          background: '#fff', flexShrink: 0,
        }}>
          <span style={{ fontSize: 13, color: '#475569' }}>
            <strong style={{ color: '#1e293b' }}>{breadcrumb[breadcrumb.length - 1]?.label}</strong>
            {!showFolderCards && selectedNode?.collection && ` — ${fileTotal} 个文档`}
          </span>
          <div style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center' }}>
            <Input.Search placeholder="搜索文件名..." style={{ width: 160 }} size="small"
              value={searchText} onSearch={handleSearch} onChange={e => setSearchText(e.target.value)} allowClear />
            <Badge count={activeUploadCount} size="small">
              <Button
                size="small"
                icon={<HistoryOutlined />}
                onClick={() => {
                  setUploadTaskDrawerOpen(true);
                  loadUploadTasks({ autoRecover: false });
                }}
              >
                上传任务
              </Button>
            </Badge>
            <Button type="primary" size="small" icon={<UploadOutlined />}
              disabled={!selectedNode?.collection}
              title={!selectedNode?.collection ? '请先选择具体分类' : ''}
              onClick={() => { setUploadOpen(true); }}>上传</Button>
          </div>
        </div>

        {/* 文件区域 */}
        <div style={{ flex: 1, overflow: 'auto', background: '#fff' }}>
          {showFolderCards ? renderFolderCards() : !selectedNode?.collection ? (
            <div style={{ textAlign: 'center', color: '#94a3b8', padding: '80px 0' }}>
              <div style={{ fontSize: 48, marginBottom: 16 }}>📂</div>
              <div style={{ fontSize: 14 }}>请在左侧选择一个分类查看文件</div>
            </div>
          ) : (
            <Table columns={columns} size="small"
              rowKey={(record) => record.file_id || record.document_id || record.filename}
              dataSource={files}
              loading={fileLoading}
              locale={{ emptyText: '暂无数据' }}
              pagination={{
                current: filePage, pageSize: 20, total: fileTotal,
                showTotal: t => `共 ${t} 条`, size: 'small',
                showSizeChanger: false,
                onChange: p => { setFilePage(p); loadFiles(selectedNode.collection, p, searchText, selectedNode); },
              }}
            />
          )}
        </div>
      </div>

      {/* 上传弹窗 */}
      <Modal
        key={selectedNode?.key || '__root__'}
        title={`📁 上传到：${selectedNode?.name || '知识库'}`}
        open={uploadOpen} width={620} destroyOnClose
        onCancel={() => { setUploadOpen(false); uploadForm.resetFields(); }}
        confirmLoading={fileLoading}
        onOk={async () => {
          try {
            const values = await uploadForm.validateFields();
            const fileList = values.file?.fileList || [];
            if (fileList.length === 0) { message.warning('请选择文件'); return; }
            if (fileList.length > 1) { message.warning('当前仅支持单文件上传'); return; }

            const selectedCollection = values.collection || selectedNode?.collection || '';
            if (!selectedCollection) { message.warning('请选择文档类型'); return; }
            const routeInfo = COLLECTION_ROUTE_MAP[selectedCollection] || {};

            const permissionLevel = ACCESS_LEVEL_TO_PERMISSION[values.access_level || selectedNode?.access_level];
            if (!permissionLevel) {
              message.warning('请选择权限级别');
              return;
            }
            setFileLoading(true);

            const formData = new FormData();
            const uploadFile = fileList[0].originFileObj;
            const uploadFileName = uploadFile?.name || fileList[0].name || '待上传文件';
            formData.append('files', uploadFile);
            formData.append('collection_type', routeInfo.uploadCollectionType || selectedCollection);
            formData.append('permission_level', permissionLevel);
            if (values.sub_category) {
              formData.append('selected_category', values.sub_category);
            }
            const { optionValues, metadataValues } = splitUploadMetadataValues(values, uploadMetadataFields);
            Object.entries(optionValues).forEach(([key, value]) => {
              formData.append(key, value);
            });
            if (selectedCollection === 'video_description' && values.video_description_text) {
              formData.append('video_description_text', values.video_description_text);
              formData.append('video_keyword_1', values.video_keyword_1);
              formData.append('video_keyword_2', values.video_keyword_2);
              formData.append('video_keyword_3', values.video_keyword_3);
            }
            formData.append('metadata', JSON.stringify(metadataValues));

            setUploadOpen(false);
            setShowProgressModal(true);
            setIngestProgress({
              mode: 'batch',
              phase: 'upload',
              status: 'uploading',
              batchStatus: 'uploading',
              progress: 0,
              uploadProgress: 0,
              message: `正在上传文件：${uploadFileName}`,
              batchId: '',
              items: [],
            });

            const res = await uploadKnowledgeBatch(formData, {
              onUploadProgress: (event) => {
                const totalBytes = Number(event.total) || 0;
                if (!totalBytes) {
                  setIngestProgress((prev) => ({
                    ...(prev || {}),
                    phase: 'upload',
                    status: 'uploading',
                    message: `正在上传文件：${uploadFileName}`,
                  }));
                  return;
                }
                const uploadPercent = Math.min(100, Math.round((event.loaded / totalBytes) * 100));
                setIngestProgress((prev) => ({
                  ...(prev || {}),
                  phase: uploadPercent >= 100 ? 'create_task' : 'upload',
                  status: 'uploading',
                  progress: uploadPercent,
                  uploadProgress: uploadPercent,
                  message: uploadPercent >= 100
                    ? '文件已上传，正在创建入库任务...'
                    : `正在上传文件：${uploadFileName}`,
                }));
              },
            });
            const data = res.data || res;
            if (data?.batch_id) {
              uploadForm.resetFields();
              startBatchPolling(data.batch_id, selectedCollection);
              loadUploadTasks({ autoRecover: false });
            } else {
              setIngestProgress((prev) => ({
                ...(prev || {}),
                phase: 'completed',
                status: 'completed',
                progress: 100,
                message: '上传入库任务已提交',
              }));
              uploadForm.resetFields();
              clearKnowledgePageCache();
              loadFiles(selectedCollection, 1, '', selectedNode);
              loadNodeCounts({ force: true });
            }
          } catch (e) {
            if (e?.errorFields) return;
            const errorMessage = e?.response?.data?.detail || e?.message || '未知错误';
            setShowProgressModal(true);
            setIngestProgress((prev) => ({
              ...(prev || {}),
              phase: 'upload',
              status: 'failed',
              batchStatus: 'failed',
              progress: prev?.progress || 0,
              message: `上传失败：${errorMessage}`,
              items: prev?.items || [],
            }));
            message.error('上传失败：' + errorMessage);
          } finally {
            setFileLoading(false);
          }
        }}
        okText="开始上传并入库"
        cancelText="取消"
      >
        {(() => {
          const disabledAccess     = !!uploadInitValues.access_level;
          const disabledCollection = !!uploadInitValues.collection;
          const disabledSubCat     = !!uploadInitValues.sub_category;
          const effectiveUploadCollection = watchedCollection || uploadInitValues.collection;
          const isVideoUpload = effectiveUploadCollection === 'video_description';
          const subCatOpts = toOpts(SUB_CATEGORY_MAP[effectiveUploadCollection] || []);

          return (
            <Form form={uploadForm} layout="vertical"
              initialValues={uploadInitValues}
              style={{ marginTop: 8 }}>

              {/* ── 文件归属 ── */}
              <Divider orientation="left" plain
                style={{ fontSize: 12, color: '#94a3b8', margin: '0 0 12px' }}>
                文件归属
              </Divider>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12 }}>
                <Form.Item name="access_level" label="权限级别"
                  rules={[{ required: true, message: '请选择权限级别' }]}>
                  <Select disabled={disabledAccess} options={ACCESS_LEVEL_OPTIONS}
                    {...SEARCHABLE_SELECT_PROPS}
                    placeholder="请选择" />
                </Form.Item>

                <Form.Item name="collection" label="文档类型"
                  rules={[{ required: true, message: '请选择文档类型' }]}>
                  <Select disabled={disabledCollection} options={COLLECTION_OPTIONS}
                    {...SEARCHABLE_SELECT_PROPS}
                    placeholder="请选择"
                    onChange={() => uploadForm.setFieldValue('sub_category', undefined)} />
                </Form.Item>

                <Form.Item
                  name="sub_category"
                  label="子分类"
                  rules={[{ required: true, message: '请选择子分类' }]}
                >
                  <Select disabled={disabledSubCat} options={subCatOpts}
                    {...SEARCHABLE_SELECT_PROPS}
                    placeholder={subCatOpts.length ? '请选择' : '—'}
                    allowClear />
                </Form.Item>
              </div>

              {/* ── 文档信息 ── */}
              <Divider orientation="left" plain
                style={{ fontSize: 12, color: '#94a3b8', margin: '4px 0 12px' }}>
                文档信息（可选）
              </Divider>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12 }}>
                {normalizeMetadataFields(uploadMetadataFields).map((field) => (
                  <Form.Item key={field.key} name={field.key} label={field.label}>
                    {field.input_type === 'select' ? (
                      <Select
                        options={toOpts(field.options)}
                        allowClear
                        {...SEARCHABLE_SELECT_PROPS}
                        placeholder={field.placeholder || '请选择'}
                      />
                    ) : (
                      <Input placeholder={field.placeholder || '选填'} />
                    )}
                  </Form.Item>
                ))}
              </div>

              {isVideoUpload && (
                <>
                  <Divider orientation="left" plain
                    style={{ fontSize: 12, color: '#94a3b8', margin: '4px 0 12px' }}>
                    视频描述
                  </Divider>
                  <Form.Item
                    name="video_description_text"
                    label="视频描述"
                    rules={[{ required: true, message: '请输入视频描述' }]}
                  >
                    <Input.TextArea rows={4} placeholder="请输入视频内容描述" />
                  </Form.Item>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12 }}>
                    <Form.Item
                      name="video_keyword_1"
                      label="关键词1"
                      rules={[{ required: true, message: '请输入关键词1' }]}
                    >
                      <Input placeholder="请输入关键词1" />
                    </Form.Item>
                    <Form.Item
                      name="video_keyword_2"
                      label="关键词2"
                      rules={[{ required: true, message: '请输入关键词2' }]}
                    >
                      <Input placeholder="请输入关键词2" />
                    </Form.Item>
                    <Form.Item
                      name="video_keyword_3"
                      label="关键词3"
                      rules={[{ required: true, message: '请输入关键词3' }]}
                    >
                      <Input placeholder="请输入关键词3" />
                    </Form.Item>
                  </div>
                </>
              )}

              {/* ── 文件上传 ── */}
              <Divider orientation="left" plain
                style={{ fontSize: 12, color: '#94a3b8', margin: '4px 0 12px' }}>
                文件上传
              </Divider>
              <Form.Item name="file" rules={[{ required: true, message: '请选择文件' }]}>
                <Upload.Dragger
                  maxCount={1}
                  beforeUpload={() => false}
                  accept={isVideoUpload ? '.mp4' : undefined}
                >
                  <p className="ant-upload-drag-icon"><InboxOutlined /></p>
                  <p className="ant-upload-text">将文件拖拽到此处，或点击选择文件</p>
                  <p className="ant-upload-hint">
                    {isVideoUpload
                      ? '视频当前仅支持上传MP4文件'
                      : '文件格式按文档类型校验，当前仅支持单文件上传'}
                  </p>
                </Upload.Dragger>
              </Form.Item>

            </Form>
          );
        })()}
      </Modal>

      <Drawer
        title="上传任务"
        open={uploadTaskDrawerOpen}
        width={760}
        onClose={() => setUploadTaskDrawerOpen(false)}
        extra={
          <Button size="small" icon={<ReloadOutlined />} onClick={() => loadUploadTasks({ autoRecover: false })}>
            刷新
          </Button>
        }
      >
        <Table
          size="small"
          rowKey="batch_id"
          columns={uploadTaskColumns}
          dataSource={uploadTasks}
          loading={uploadTasksLoading}
          pagination={{ pageSize: 8, showSizeChanger: false }}
        />
      </Drawer>

      {/* 入库进度弹窗 */}
      <Modal
        title="上传与入库进度"
        open={showProgressModal}
        width={520}
        footer={
          ingestProgress?.status === 'failed'
            ? [
                <Button key="close" onClick={() => setShowProgressModal(false)}>关闭</Button>,
                ingestProgress?.batchId && FAILED_UPLOAD_STATUSES.has(ingestProgress.batchStatus)
                  ? <Button key="retry" type="primary" onClick={() => retryBatch(ingestProgress.batchId, ingestProgress.collectionName || selectedNode?.collection)}>重试失败项</Button>
                  : <Button key="reupload" type="primary" onClick={() => { setShowProgressModal(false); setUploadOpen(true); }}>重新上传</Button>,
              ]
            : ingestProgress?.status === 'completed'
              ? [<Button key="ok" type="primary" onClick={() => setShowProgressModal(false)}>完成</Button>]
              : [
                  <Button key="background" onClick={() => setShowProgressModal(false)}>后台运行</Button>,
                  ingestProgress?.batchId ? (
                    <Popconfirm
                      key="delete"
                      title="删除上传任务"
                      description={getUploadDeleteDescription(ingestProgress.batchStatus)}
                      okText="删除"
                      cancelText="取消"
                      okButtonProps={{ danger: true }}
                      onConfirm={() => deleteBatch({
                        batch_id: ingestProgress.batchId,
                        status: ingestProgress.batchStatus,
                        collection_name: ingestProgress.collectionName,
                        collection_type: ingestProgress.collectionType,
                      })}
                    >
                      <Button danger icon={<DeleteOutlined />}>停止并删除</Button>
                    </Popconfirm>
                  ) : null,
                ]
        }
        closable
        maskClosable
        onCancel={() => setShowProgressModal(false)}
      >
        {ingestProgress && (
          <div style={{ padding: '8px 0' }}>
            {ingestProgress.batchId && (
              <div style={{ marginBottom: 16, fontSize: 13, color: '#64748b' }}>
                任务ID：<strong style={{ color: '#1e293b' }}>{ingestProgress.batchId}</strong>
              </div>
            )}

            <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 10 }}>
              <Tag color={ingestProgress.phase === 'upload' ? 'blue' : ingestProgress.phase === 'create_task' ? 'gold' : 'green'}>
                {ingestProgress.phase === 'upload'
                  ? '文件上传'
                  : ingestProgress.phase === 'create_task'
                    ? '创建任务'
                    : '后台入库'}
              </Tag>
              <span style={{ color: '#475569', fontSize: 13 }}>
                {ingestProgress.displayStatus || ingestProgress.activeStepName || ingestProgress.message}
              </span>
            </div>

            <Progress
              percent={Number(ingestProgress.progress) || 0}
              status={
                ingestProgress.status === 'failed' ? 'exception'
                  : ingestProgress.status === 'completed' ? 'success'
                    : 'active'
              }
              showInfo
              style={{ marginBottom: 16 }}
            />

            {/* 状态文字 */}
            <div style={{ textAlign: 'center', fontSize: 13, color: '#475569', minHeight: 20 }}>
              {ingestProgress.message}
            </div>
            {ingestProgress.activeStepCurrent && ingestProgress.activeStepTotal && (
              <div style={{ textAlign: 'center', fontSize: 12, color: '#94a3b8', marginTop: 4 }}>
                Embedding {ingestProgress.activeStepCurrent} / {ingestProgress.activeStepTotal}
              </div>
            )}

            {Array.isArray(ingestProgress.items) && ingestProgress.items.length > 0 && (
              <div style={{ marginTop: 16, border: '1px solid #e2e8f0', borderRadius: 8, overflow: 'hidden' }}>
                {ingestProgress.items.slice(0, 5).map((item) => (
                  <div
                    key={item.task_id || item.filename}
                    style={{
                      display: 'grid',
                      gridTemplateColumns: 'minmax(0, 1fr) 88px 64px',
                      alignItems: 'center',
                      gap: 12,
                      padding: '10px 12px',
                      borderTop: '1px solid #f1f5f9',
                      fontSize: 12,
                    }}
                  >
                    <div style={{ minWidth: 0 }}>
                      <div style={{ color: '#334155', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{item.filename}</div>
                      <div style={{ color: '#64748b', marginTop: 2 }}>{item.message || item.progress_detail || item.display_step_name || '-'}</div>
                    </div>
                    <Tag color={item.status === 'failed' ? 'error' : item.status === 'done' ? 'success' : 'processing'} style={{ margin: 0 }}>
                      {item.display_status || item.status}
                    </Tag>
                    <span style={{ color: '#475569', textAlign: 'right' }}>{Number(item.progress_percent) || 0}%</span>
                  </div>
                ))}
              </div>
            )}
            {ingestProgress.status === 'completed' && (
              <Result status="success" subTitle={ingestProgress.message} style={{ padding: '12px 0 0' }} />
            )}
          </div>
        )}
      </Modal>
    </div>
  );
}
