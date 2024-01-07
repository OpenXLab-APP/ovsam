import gradio as gr
import numpy as np
from PIL import ImageDraw, Image

import torch
import torch.nn.functional as F

# mm libs
from mmdet.registry import MODELS
from mmengine import Config
from mmengine.structures import InstanceData

from ext.class_names.lvis_list import LVIS_CLASSES

LVIS_NAMES = LVIS_CLASSES

# Description
title = "<center><strong><font size='8'>Open-Vocabulary SAM<font></strong></center>"

css = "h1 { text-align: center } .about { text-align: justify; padding-left: 10%; padding-right: 10%; }"

model_cfg = Config.fromfile('app/configs/sam_r50x16_fpn.py')

examples = [
    ["app/assets/sa_01.jpg"],
    ["app/assets/sa_224028.jpg"],
    ["app/assets/sa_227490.jpg"],
    ["app/assets/sa_228025.jpg"],
    ["app/assets/sa_234958.jpg"],
    ["app/assets/sa_235005.jpg"],
    ["app/assets/sa_235032.jpg"],
    ["app/assets/sa_235036.jpg"],
    ["app/assets/sa_235086.jpg"],
    ["app/assets/sa_235094.jpg"],
    ["app/assets/sa_235113.jpg"],
    ["app/assets/sa_235130.jpg"],
]
model = MODELS.build(model_cfg.model)
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
model = model.to(device=device)
model = model.eval()
model.init_weights()

mean = torch.tensor([123.675, 116.28, 103.53], device=device)[:, None, None]
std = torch.tensor([58.395, 57.12, 57.375], device=device)[:, None, None]


class IMGState:
    def __init__(self):
        self.img = None
        self.img_feat = None
        self.selected_points = []
        self.selected_points_labels = []
        self.selected_bboxes = []

        self.available_to_set = True

    def set_img(self, img, img_feat):
        self.img = img
        self.img_feat = img_feat

        self.available_to_set = False

    def clear(self):
        self.img = None
        self.img_feat = None
        self.selected_points = []
        self.selected_points_labels = []
        self.selected_bboxes = []

        self.available_to_set = True

    def clean(self):
        self.selected_points = []
        self.selected_points_labels = []
        self.selected_bboxes = []

    @property
    def available(self):
        return self.available_to_set


IMG_SIZE = 1024


def get_points_with_draw(image, img_state, evt: gr.SelectData):
    w, h = image.size
    assert max(w, h) == IMG_SIZE, f"{w} x {h}"
    label = 'Add Mask'

    x, y = evt.index[0], evt.index[1]
    print(f"Point: {x}_{y}")
    point_radius, point_color = 10, (97, 217, 54) if label == "Add Mask" else (237, 34, 13)

    img_state.selected_points.append([x, y])
    img_state.selected_points_labels.append(1 if label == "Add Mask" else 0)

    draw = ImageDraw.Draw(image)
    draw.ellipse(
        [(x - point_radius, y - point_radius), (x + point_radius, y + point_radius)],
        fill=point_color,
    )
    return image


def get_bbox_with_draw(image, img_state, evt: gr.SelectData):
    x, y = evt.index[0], evt.index[1]
    point_radius, point_color, box_outline = 5, (237, 34, 13), 2
    box_color = (237, 34, 13)

    if len(img_state.selected_bboxes) in [0, 1]:
        img_state.selected_bboxes.append([x, y])
    elif len(img_state.selected_bboxes) == 2:
        img_state.selected_bboxes = [[x, y]]
        image = Image.fromarray(img_state.img)
    else:
        raise ValueError(f"Cannot be {len(img_state.selected_bboxes)}")

    print(f"box_list: {img_state.selected_bboxes}")

    draw = ImageDraw.Draw(image)
    draw.ellipse(
        [(x - point_radius, y - point_radius), (x + point_radius, y + point_radius)],
        fill=point_color,
    )

    if len(img_state.selected_bboxes) == 2:
        box_points = img_state.selected_bboxes
        bbox = (min(box_points[0][0], box_points[1][0]),
                min(box_points[0][1], box_points[1][1]),
                max(box_points[0][0], box_points[1][0]),
                max(box_points[0][1], box_points[1][1]),
                )
        draw.rectangle(
            bbox,
            outline=box_color,
            width=box_outline
        )
    return image


def segment_with_points(
        image,
        img_state,
):
    assert not img_state.available
    output_img = img_state.img
    h, w = output_img.shape[:2]

    input_points = torch.tensor(img_state.selected_points, dtype=torch.float32, device=device)

    prompts = InstanceData(
        point_coords=input_points[None],
    )
    masks, cls_pred = model.extract_masks(img_state.img_feat, prompts)

    masks = masks[0, 0, :h, :w]
    masks = masks > 0.5

    cls_pred = cls_pred[0][0]
    scores, indices = torch.topk(cls_pred, 1)
    scores, indices = scores.tolist(), indices.tolist()
    names = []
    for ind in indices:
        names.append(LVIS_NAMES[ind].replace('_', ' '))

    cls_info = ""
    for name, score in zip(names, scores):
        cls_info += "{} ({:.2f})".format(name, score)

    rgb_shape = tuple(list(masks.shape) + [3])
    color = np.zeros(rgb_shape, dtype=np.uint8)
    color[masks] = np.array([97, 217, 54])
    # color[masks] = np.array([217, 90, 54])
    output_img = (output_img * 0.7 + color * 0.3).astype(np.uint8)

    output_img = Image.fromarray(output_img)
    return image, output_img, cls_info


def segment_with_bbox(
        image,
        img_state
):
    assert not img_state.available
    if len(img_state.selected_bboxes) != 2:
        return image, None, ""
    output_img = img_state.img
    h, w = output_img.shape[:2]

    box_points = img_state.selected_bboxes
    bbox = (
        min(box_points[0][0], box_points[1][0]),
        min(box_points[0][1], box_points[1][1]),
        max(box_points[0][0], box_points[1][0]),
        max(box_points[0][1], box_points[1][1]),
    )
    input_bbox = torch.tensor(bbox, dtype=torch.float32, device=device)

    prompts = InstanceData(
        bboxes=input_bbox[None],
    )
    masks, cls_pred = model.extract_masks(img_state.img_feat, prompts)

    masks = masks[0, 0, :h, :w]
    masks = masks > 0.5

    cls_pred = cls_pred[0][0]
    scores, indices = torch.topk(cls_pred, 1)
    scores, indices = scores.tolist(), indices.tolist()
    names = []
    for ind in indices:
        names.append(LVIS_NAMES[ind].replace('_', ' '))

    cls_info = ""
    for name, score in zip(names, scores):
        cls_info += "{} ({:.2f})\n".format(name, score)

    rgb_shape = tuple(list(masks.shape) + [3])
    color = np.zeros(rgb_shape, dtype=np.uint8)
    color[masks] = np.array([97, 217, 54])
    # color[masks] = np.array([217, 90, 54])
    output_img = (output_img * 0.7 + color * 0.3).astype(np.uint8)

    output_img = Image.fromarray(output_img)
    return image, output_img, cls_info


def extract_img_feat(img, img_state):
    w, h = img.size
    scale = IMG_SIZE / max(w, h)
    new_w = int(w * scale)
    new_h = int(h * scale)
    img = img.resize((new_w, new_h), resample=Image.Resampling.BILINEAR)
    img_numpy = np.array(img)
    print(f"Successfully loaded an image with size {new_w} x {new_h}")

    img_tensor = torch.tensor(img_numpy, device=device, dtype=torch.float32).permute((2, 0, 1))[None]
    img_tensor = (img_tensor - mean) / std
    img_tensor = F.pad(img_tensor, (0, IMG_SIZE - new_w, 0, IMG_SIZE - new_h), 'constant', 0)
    feat_dict = model.extract_feat(img_tensor)
    img_state.set_img(img_numpy, feat_dict)
    print(f"Successfully generated the image feats.")
    return img, None, "Please try to click something."


def clear_everything(img_state):
    img_state.clear()
    return None, None, "Please try to click something."


def clean_prompts(img_state):
    img_state.clean()
    return Image.fromarray(img_state.img), None, "Please try to click something."


def register_point_mode():
    img_state_points = gr.State(IMGState())
    img_state_bbox = gr.State(IMGState())
    with gr.Row():
        with gr.Column(scale=1):
            gr.Markdown(title)

    # Point mode tab
    with gr.Tab("Point mode"):
        with gr.Row(variant="panel"):
            with gr.Column(scale=1):
                cond_img_p = gr.Image(label="Input Image", height=512, type="pil")

            with gr.Column(scale=1):
                segm_img_p = gr.Image(label="Segment", interactive=False, height=512, type="pil")

        with gr.Row():
            with gr.Column():
                with gr.Row():
                    with gr.Column():
                        clean_btn_p = gr.Button("Clean Prompts", variant="secondary")
                        clear_btn_p = gr.Button("Restart", variant="secondary")
            with gr.Column():
                cls_info = gr.Textbox("", label='Labels')

        with gr.Row():
            with gr.Column():
                gr.Markdown("Try some of the examples below ⬇️")
                gr.Examples(
                    examples=examples,
                    inputs=[cond_img_p, img_state_points],
                    outputs=[cond_img_p, segm_img_p, cls_info],
                    examples_per_page=12,
                    fn=extract_img_feat,
                    run_on_click=True
                )

    # box mode tab
    with gr.Tab("Box mode"):
        with gr.Row(variant="panel"):
            with gr.Column(scale=1):
                cond_img_bbox = gr.Image(label="Input Image", height=512, type="pil")

            with gr.Column(scale=1):
                segm_img_bbox = gr.Image(label="Segment", interactive=False, height=512, type="pil")

        with gr.Row():
            with gr.Column():
                with gr.Row():
                    with gr.Column():
                        clean_btn_bbox = gr.Button("Clean Prompts", variant="secondary")
                        clear_btn_bbox = gr.Button("Restart", variant="secondary")
            with gr.Column():
                cls_info_bbox = gr.Textbox("", label='Labels')

        with gr.Row():
            with gr.Column():
                gr.Markdown("Try some of the examples below ⬇️")
                gr.Examples(
                    examples=examples,
                    inputs=[cond_img_bbox, img_state_bbox],
                    outputs=[cond_img_bbox, segm_img_bbox, cls_info_bbox],
                    examples_per_page=12,
                    fn=extract_img_feat,
                    run_on_click=True
                )

    # extract image feature
    cond_img_p.upload(
        extract_img_feat,
        [cond_img_p, img_state_points],
        outputs=[cond_img_p, segm_img_p, cls_info]
    )
    cond_img_bbox.upload(
        extract_img_feat,
        [cond_img_bbox, img_state_bbox],
        outputs=[cond_img_bbox, segm_img_bbox, cls_info]
    )

    # get user added points
    cond_img_p.select(
        get_points_with_draw,
        [cond_img_p, img_state_points],
        cond_img_p
    ).then(
        segment_with_points,
        inputs=[cond_img_p, img_state_points],
        outputs=[cond_img_p, segm_img_p, cls_info]
    )
    cond_img_bbox.select(
        get_bbox_with_draw,
        [cond_img_bbox, img_state_bbox],
        cond_img_bbox
    ).then(
        segment_with_bbox,
        inputs=[cond_img_bbox, img_state_bbox],
        outputs=[cond_img_bbox, segm_img_bbox, cls_info_bbox]
    )

    # clean prompts
    clean_btn_p.click(
        clean_prompts,
        inputs=[img_state_points],
        outputs=[cond_img_p, segm_img_p, cls_info]
    )
    clean_btn_bbox.click(
        clean_prompts,
        inputs=[img_state_bbox],
        outputs=[cond_img_bbox, segm_img_bbox, cls_info_bbox]
    )

    # clear
    clear_btn_p.click(
        clear_everything,
        inputs=[img_state_points],
        outputs=[cond_img_p, segm_img_p, cls_info]
    )
    cond_img_p.clear(
        clear_everything,
        inputs=[img_state_points],
        outputs=[cond_img_p, segm_img_p, cls_info]
    )
    segm_img_p.clear(
        clear_everything,
        inputs=[img_state_points],
        outputs=[cond_img_p, segm_img_p, cls_info]
    )
    clear_btn_bbox.click(
        clear_everything,
        inputs=[img_state_bbox],
        outputs=[cond_img_bbox, segm_img_bbox, cls_info_bbox]
    )
    cond_img_bbox.clear(
        clear_everything,
        inputs=[img_state_bbox],
        outputs=[cond_img_bbox, segm_img_bbox, cls_info_bbox]
    )
    segm_img_bbox.clear(
        clear_everything,
        inputs=[img_state_bbox],
        outputs=[cond_img_bbox, segm_img_bbox, cls_info_bbox]
    )


if __name__ == '__main__':
    with gr.Blocks(css=css, title="Open-Vocabulary SAM") as demo:
        register_point_mode()
    demo.queue()
    demo.launch(show_api=False)
