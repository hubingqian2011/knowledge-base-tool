from sqlalchemy.orm import Session
from ..model.parameter import Parameter, ParameterSet
from .base_repository import BaseRepository
from util.logging.logger import get_logger

logger = get_logger(__name__)

class ParameterRepository(BaseRepository):
    """参数仓库类，继承BaseRepository并提供特定的参数管理功能"""

    def __init__(self, session: Session = None, model_class=None):
        """初始化参数仓库

        Args:
            session: 数据库会话
            model_class: 模型类，对于ParameterRepository通常不直接使用基础CRUD
        """
        # ParameterRepository主要处理Parameter和ParameterSet两个模型
        # 不设置特定的model_class，直接使用session操作
        self.session = session

    def create_parameter_set(self, name: str, description: str = None) -> ParameterSet:
        """创建参数集"""
        try:
            parameter_set = ParameterSet(name=name, description=description)
            self.session.add(parameter_set)
            self.session.flush()
            logger.info(f"创建参数集成功: name={name}, id={parameter_set.id}")
            return parameter_set
        except Exception as e:
            logger.error(f"创建参数集失败: name={name}, error={str(e)}")
            raise

    def get_parameter_set(self, parameter_set_id: int) -> ParameterSet:
        """根据ID获取参数集"""
        return self.session.query(ParameterSet).filter(ParameterSet.id == parameter_set_id).first()

    def get_all_parameter_sets(self):
        """获取所有参数集"""
        return self.session.query(ParameterSet).all()

    def create_parameter(self, name: str, parameter_set_id: int, description: str = None, parent_id: int = None) -> Parameter:
        """创建参数"""
        try:
            parameter = Parameter(
                name=name,
                parameter_set_id=parameter_set_id,
                description=description,
                parameter_parent_id=parent_id
            )
            self.session.add(parameter)
            self.session.flush()
            logger.info(f"创建参数成功: name={name}, id={parameter.id}, parameter_set_id={parameter_set_id}")
            return parameter
        except Exception as e:
            logger.error(f"创建参数失败: name={name}, parameter_set_id={parameter_set_id}, error={str(e)}")
            raise

    def get_parameters_by_set(self, parameter_set_id: int):
        """根据参数集ID获取所有参数"""
        return self.session.query(Parameter).filter(Parameter.parameter_set_id == parameter_set_id).all()

    def get_root_parameters(self, parameter_set_id: int):
        """获取参数集中的所有根参数（没有父参数的参数）"""
        return self.session.query(Parameter).filter(
            Parameter.parameter_set_id == parameter_set_id,
            Parameter.parameter_parent_id == None
        ).all()

    def get_children_parameters(self, parameter_id: int):
        """获取指定参数的所有子参数"""
        return self.session.query(Parameter).filter(Parameter.parameter_parent_id == parameter_id).all()

    def get_parameter_tree(self, parameter_set_id: int):
        """获取参数集的完整树状结构"""
        def build_tree(parameters, parent_id=None):
            tree = {}
            for param in parameters:
                if param.parameter_parent_id == parent_id:
                    children = build_tree(parameters, param.id)
                    if children:
                        if param.description == "LIST":
                            tree[param.name] = [children]
                        else:
                            tree[param.name] = children
                    else:
                        tree[param.name] = param.description
            return tree

        parameters = self.get_parameters_by_set(parameter_set_id)
        return build_tree(parameters)

    def update_parameter(self, parameter_id: int, name: str = None, description: str = None, parent_id: int = None) -> Parameter:
        """更新参数信息"""
        parameter = self.session.query(Parameter).filter(Parameter.id == parameter_id).first()
        if parameter:
            if name:
                parameter.name = name
            if description:
                parameter.description = description
            if parent_id is not None:
                parameter.parameter_parent_id = parent_id
            self.session.flush()
        return parameter

    def delete_parameter(self, parameter_id: int) -> bool:
        """删除参数（包括其所有子参数）"""
        try:
            parameter = self.session.query(Parameter).filter(Parameter.id == parameter_id).first()
            if parameter:
                # 删除所有子参数
                children = self.get_children_parameters(parameter_id)
                deleted_count = 0
                for child in children:
                    if self.delete_parameter(child.id):
                        deleted_count += 1
                self.session.delete(parameter)
                self.session.flush()
                logger.info(f"删除参数成功: parameter_id={parameter_id}, name={parameter.name}, deleted_children={deleted_count}")
                return True
            logger.warning(f"参数不存在: parameter_id={parameter_id}")
            return False
        except Exception as e:
            logger.error(f"删除参数失败: parameter_id={parameter_id}, error={str(e)}")
            return False 