"""Three compact Office capabilities in the existing registry's office group."""
FORMATS = {
    "docx": "data.elements: {type:heading|paragraph|bullet|numbered|table|page_break,text,level,runs:[{text,bold,italic}],rows}; data.style:{font,font_size}. Changes: append(elements), set_text(element_id,text), set_table_cell(element_id,row,column,text).",
    "xlsx": "data.sheets:[{name,rows,header,column_widths,freeze_panes,autofilter,formats:[{range,style}]}]. Formula strings start with =; date values use {date:ISO} or {datetime:ISO}; formulas are stored, not calculated. Changes: write_range(sheet,start,rows,style), set_cell(sheet,cell,value,style), append_rows(sheet,rows), add_sheet(name,rows), format_range(sheet,range,style), format_sheet(sheet,column_widths,freeze_panes,autofilter). Style:{bold,italic,color,fill,number_format,wrap}.",
    "pptx": "data.slides:[{title,text,paragraphs:[{text,level,bold,italic,bullet}],table:[[text]],images:[{path,x,y,width,height}],notes}]. Uses a clean 16:9 layout. Changes: add_slide(slide), set_text(slide_id,shape_id,text|paragraphs,font_size), add_content(slide_id,content:{title,text,paragraphs,table,images,notes}).",
}


def office_schemas():
    return [{"type":"function","function":{"name":"office_"+fmt,
        "description":"Native "+fmt.upper()+": create, bounded inspect, targeted edit; no generated scripts. "
            "Create never overwrites. Edit requires expected_sha256 from inspect; use returned element IDs. "+details,
        "parameters":{"type":"object","properties":{
            "action":{"type":"string","enum":["create","inspect","edit"]},
            "path":{"type":"string","description":"Absolute path on the selected device."},
            "device_id":{"type":"string"},
            "data":{"type":"object","description":"Structured creation content; see tool description."},
            "changes":{"type":"array","maxItems":100,"items":{"type":"object"}},
            "expected_sha256":{"type":"string","description":"Exact revision returned by inspect, required for edit."},
            "selector":{"type":"object","description":"offset,limit (<=50); DOCX element_id,text_offset,row_offset,column_offset; XLSX sheet,range (<=100 cells),text_offset; PPTX slide_id,shape_id,text_offset,row_offset,column_offset."}},
            "required":["action","path"],"additionalProperties":False}}} for fmt,details in FORMATS.items()]


def office_metadata():
    return {"office_"+fmt:{"category":"office","tool_type":"typed","tool_label":"Office "+fmt.upper(),
        "purpose":"Create, inspect or edit "+fmt.upper()+" on the owned device without generated code",
        "when_to_use":["standard "+fmt.upper()+" content operations"],"returns":"bounded structure/revision or verified final file receipt",
        "danger":"write"} for fmt in FORMATS}


def validate_office_args(args):
    import json
    import re
    if args.get("action") not in {"create","inspect","edit"} or not isinstance(args.get("path"),str):
        return "invalid_office_action_or_path"
    if (not isinstance(args.get("data",{}),dict) or not isinstance(args.get("changes",[]),list)
            or not isinstance(args.get("selector",{}),dict)
            or any(not isinstance(c,dict) for c in args.get("changes",[]))):
        return "invalid_office_parameters"
    if args["action"] == "edit" and not re.fullmatch(r"[0-9a-f]{64}",str(args.get("expected_sha256",""))):
        return "office_edit_revision_required"
    if len(json.dumps(args,ensure_ascii=False).encode("utf-8")) > 256000:
        return "office_request_too_large_use_targeted_operations"
    slides=args.get("data",{}).get("slides",[])
    if not isinstance(slides,list) or any(not isinstance(s,dict) for s in slides):
        return "invalid_office_slides"
    for item in [*slides,*args.get("changes",[])]:
        for spec in (item,item.get("slide",{}),item.get("content",{})):
            if not isinstance(spec,dict): return "invalid_office_content"
            images=spec.get("images",[])
            if not isinstance(images,list) or any(not isinstance(i,dict) or not isinstance(i.get("path"),str) for i in images):
                return "invalid_office_images"
    return None
