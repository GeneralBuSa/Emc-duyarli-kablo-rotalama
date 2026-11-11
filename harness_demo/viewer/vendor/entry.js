// Entry of three.bundle.min.js (see package.json). Only what harness_demo/viewer/viewer.js imports.
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import { CSS2DRenderer, CSS2DObject } from "three/addons/renderers/CSS2DRenderer.js";

window.__three = { THREE, OrbitControls, RoomEnvironment, CSS2DRenderer, CSS2DObject };
