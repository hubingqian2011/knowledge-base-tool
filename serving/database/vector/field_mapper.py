from typing import Any, Dict


class FieldMapper:
    """字段中英文转换工具。"""

    FIELD_MAPPING = {
        "服务机构": "service_provider",
        "序列号": "serial_number",
        "派工单号": "work_order_number",
        "派工单创建日期": "work_order_create_date",
        "产品型号": "product_model",
        "出厂日期": "manufacture_date",
        "部件大类": "component_category",
        "部件小类": "component_subcategory",
        "明细": "details",
        "故障类型": "fault_type",
        "处理方式": "handling_method",
        "产品线": "product_line",
        "代次": "generation",
        "代次名称": "generation_name",
        "机型码": "machine_code",
        "机型": "machine_type",
        "系列": "series",
        "锁模力": "clamping_force",
        "特性字符": "characteristic_char",
        "控制器": "controller",
        "控制器型号": "controller_type",
        "版本": "version",
        "语言": "language",
        "使用年限": "service_life",
    }
    REVERSE_MAPPING = {value: key for key, value in FIELD_MAPPING.items()}

    @classmethod
    def chinese_to_english(cls, chinese_field: str) -> str:
        return cls.FIELD_MAPPING.get(chinese_field, chinese_field)

    @classmethod
    def english_to_chinese(cls, english_field: str) -> str:
        return cls.REVERSE_MAPPING.get(english_field, english_field)

    @classmethod
    def map_dict_keys(cls, data: Dict[str, Any], direction: str = "chinese_to_english") -> Dict[str, Any]:
        if direction == "chinese_to_english":
            return {cls.chinese_to_english(key): value for key, value in data.items()}
        if direction == "english_to_chinese":
            return {cls.english_to_chinese(key): value for key, value in data.items()}
        raise ValueError("direction must be 'chinese_to_english' or 'english_to_chinese'")

    @classmethod
    def get_mapping_dict(cls) -> Dict[str, str]:
        return cls.FIELD_MAPPING.copy()

    @classmethod
    def contains_chinese_fields(cls, data: Dict[str, Any]) -> bool:
        return any(key in cls.FIELD_MAPPING for key in data.keys())
