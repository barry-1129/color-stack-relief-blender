# -*- coding: utf-8 -*-
"""核心算法包。

- ``image_analysis`` 图片读取与降采样（依赖 bpy）
- ``color_engine``   叠色配色引擎（纯算法，可离线单测）
- ``quantize``       目标色 K-Means 量化与去孤立点（纯算法，可离线单测）
- ``mosaic``         平整色块板：聚类 N 色 → 分区 → 全色域分析（纯算法）
- ``mesh_builder``   三种模式的几何构建（依赖 bmesh）
- ``exporters``      3MF / OBJ 导出（纯标准库）
- ``lanyard``        挂绳孔后处理（依赖 bmesh）
- ``generation``     流程编排（依赖 bpy）
"""
