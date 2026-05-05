import request from './request';

// Auth
export const login = (data) => request.post('/api/auth/login', data);
export const getCurrentUser = () => request.get('/api/auth/me');

// Knowledge — Collections
export const getCollections = () => request.get('/api/admin/knowledge/collections');
export const upsertCollection = (data) => request.post('/api/admin/knowledge/collections', data);
export const getCollectionFiles = (name, params) =>
  request.get(`/api/admin/knowledge/collections/${name}/files`, { params });

// Knowledge — Tree
export const getKnowledgeTree = (params) => request.get('/api/admin/knowledge/tree', { params });
export const getKnowledgeTreeOptions = (params) => request.get('/api/admin/knowledge/tree-options', { params });
export const validateTreeMetadata = (data) => request.post('/api/admin/knowledge/tree/validate', data);

// Knowledge — Upload Batch
export const uploadKnowledgeBatch = (formData, options = {}) =>
  request.post('/api/admin/knowledge/upload-batch', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 300000,
    ...options,
  });
export const getUploadBatches = (params) => request.get('/api/admin/knowledge/upload-batches', { params });
export const getUploadBatchStatus = (batchId) => request.get(`/api/admin/knowledge/upload-batches/${batchId}`);
export const retryUploadBatch = (batchId, data = {}) =>
  request.post(`/api/admin/knowledge/upload-batches/${batchId}/retry`, data);
export const deleteUploadBatch = (batchId) =>
  request.delete(`/api/admin/knowledge/upload-batches/${batchId}`);

// Knowledge — Files (V2)
export const getFiles = (params) => request.get('/api/admin/knowledge/files', { params });
export const getFileDetail = (fileId, params) =>
  request.get(`/api/admin/knowledge/files/${fileId}`, { params });
export const deleteFile = (fileId, collectionName) =>
  request.delete(`/api/admin/knowledge/files/${fileId}`, { params: { collection_name: collectionName } });
export const previewFile = (fileId) =>
  request.get(`/api/admin/knowledge/files/${fileId}/preview`, { responseType: 'blob', timeout: 120000 });
export const retryFile = (fileId, data = {}) =>
  request.post(`/api/admin/knowledge/files/${fileId}/retry`, data);
