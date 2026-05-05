from sqlalchemy import Column, Integer, String, ForeignKey
from sqlalchemy.orm import relationship
from .database_manager import Base

class ParameterSet(Base):
    __tablename__ = "parameter_set"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, index=True)
    description = Column(String(500), nullable=True)
    
    # 建立与Parameter的关系
    parameters = relationship("Parameter", back_populates="parameter_set")

class Parameter(Base):
    __tablename__ = "parameter"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), index=True)
    description = Column(String(500), nullable=True)
    parameter_set_id = Column(Integer, ForeignKey("parameter_set.id"))
    parameter_parent_id = Column(Integer, ForeignKey("parameter.id"), nullable=True)
    
    # 建立与ParameterSet的关系
    parameter_set = relationship("ParameterSet", back_populates="parameters")
    # 建立自引用的父子关系
    parent = relationship("Parameter", remote_side=[id], backref="children") 