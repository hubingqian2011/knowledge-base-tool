from elasticsearch import Elasticsearch
from typing import Dict, List, Any, Optional
from config.config import ES_URL, ES_USER, ES_PASSWORD
from util.logging.logger import get_logger

# 仅对 analyzer 未配置类错误打一次 WARNING，避免刷屏
_analyzer_warning_logged = False


class ElasticsearchClient:
    """Elasticsearch 客户端封装类"""
    
    def __init__(
        self,
        url: str = ES_URL,
        username: Optional[str] = ES_USER,
        password: Optional[str] = ES_PASSWORD
    ):
        """
        初始化 Elasticsearch 客户端
        
        Args:
            url: Elasticsearch 服务器地址
            username: 用户名（可选）
            password: 密码（可选）
        """
        self.logger = get_logger(__name__)
        
        try:
            if username and password:
                self.es = Elasticsearch(
                    url,
                    basic_auth=(username, password),
                    verify_certs=False
                )
            else:
                self.es = Elasticsearch(url)
            
            if not self.es.ping():
                raise ConnectionError("无法连接到 Elasticsearch 服务器")
                
            self.logger.debug("Elasticsearch 客户端初始化成功")
        except Exception as e:
            self.logger.error(f"Elasticsearch 客户端初始化失败: {str(e)}")
            raise
    
    def create_index(self, index_name: str, mappings: Dict[str, Any]) -> bool:
        """
        创建索引
        
        Args:
            index_name: 索引名称
            mappings: 索引映射配置
            
        Returns:
            bool: 是否创建成功
        """
        global _analyzer_warning_logged
        try:
            if not self.es.indices.exists(index=index_name):
                self.es.indices.create(index=index_name, mappings=mappings)
                self.logger.info(f"索引 {index_name} 创建成功")
                return True
            return False
        except Exception as e:
            err_str = str(e).lower()
            if "analyzer" in err_str and ("not been configured" in err_str or "ik_max_word" in err_str or "mapper_parsing_exception" in err_str):
                if not _analyzer_warning_logged:
                    _analyzer_warning_logged = True
                    self.logger.warning(f"ES 索引创建跳过(缺少 IK 分析器): index={index_name}, error={e}")
                return False
            self.logger.error(f"创建索引失败: {str(e)}")
            return False
    
    def delete_index(self, index_name: str) -> bool:
        """
        删除索引
        
        Args:
            index_name: 索引名称
            
        Returns:
            bool: 是否删除成功
        """
        try:
            if self.es.indices.exists(index=index_name):
                self.es.indices.delete(index=index_name)
                self.logger.info(f"索引 {index_name} 删除成功")
                return True
            return False
        except Exception as e:
            self.logger.error(f"删除索引失败: {str(e)}")
            return False
    
    def index_document(self, index_name: str, document: Dict[str, Any], doc_id: Optional[str] = None) -> bool:
        """
        索引文档
        
        Args:
            index_name: 索引名称
            document: 文档内容
            doc_id: 文档ID（可选）
            
        Returns:
            bool: 是否索引成功
        """
        try:
            if doc_id:
                self.es.index(index=index_name, document=document, id=doc_id)
            else:
                self.es.index(index=index_name, document=document)
            self.logger.info(f"文档索引成功")
            return True
        except Exception as e:
            self.logger.error(f"索引文档失败: {str(e)}")
            return False
    
    def search(self, index_name: str, query: Dict[str, Any], size: int = 10) -> List[Dict[str, Any]]:
        """
        搜索文档
        
        Args:
            index_name: 索引名称
            query: 查询条件
            size: 返回结果数量
            
        Returns:
            List[Dict[str, Any]]: 搜索结果列表
        """
        try:
            response = self.es.search(
                index=index_name,
                query=query,
                size=size
            )
            return [{"source": hit["_source"], "score": hit["_score"]} for hit in response["hits"]["hits"]]
        except Exception as e:
            self.logger.error(f"搜索文档失败: {str(e)}")
            return []
    
    def update_document(self, index_name: str, doc_id: str, doc: Dict[str, Any]) -> bool:
        """
        更新文档
        
        Args:
            index_name: 索引名称
            doc_id: 文档ID
            doc: 更新的文档内容
            
        Returns:
            bool: 是否更新成功
        """
        try:
            self.es.update(index=index_name, id=doc_id, doc=doc)
            self.logger.info(f"文档更新成功")
            return True
        except Exception as e:
            self.logger.error(f"更新文档失败: {str(e)}")
            return False
    
    def delete_document(self, index_name: str, doc_id: str) -> bool:
        """
        删除文档
        
        Args:
            index_name: 索引名称
            doc_id: 文档ID
            
        Returns:
            bool: 是否删除成功
        """
        try:
            self.es.delete(index=index_name, id=doc_id)
            self.logger.info(f"文档删除成功")
            return True
        except Exception as e:
            self.logger.error(f"删除文档失败: {str(e)}")
            return False 