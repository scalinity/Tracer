// Tube glass: plain transparency (the frame keeps no transmission pass) whose opacity rises toward
// grazing angles, as real glass reflects more there. A tube reads by its edges and stays clear where
// you look straight through it at the light and the carrier inside.
//
// Depth of field reads the scene's depth, and transparent glass writes none: a tube would take the
// depth of the room behind it and blur away with it. So the outer skins write depth along their
// edge band only, and discard the clear middle, where the background seen through the glass keeps
// its own blur. On light paper a lit glass reflects the bright studio and disappears, so each glass
// mesh also has an unlit paper twin, shown only in Studio, whose edges are a cool gray instead.

import * as THREE from 'three/webgpu';
import { abs, Discard, float, Fn, If, mix, normalView, positionViewDirection, vec3, vec4 } from 'three/tsl';
import { followEnvironment } from '../kit/materials';

/** Below this opacity a fragment of an outer skin is not drawn, so it writes no depth. */
const EDGE_CUT = 0.16;

const grazing = (power: number) => float(1).sub(abs(normalView.dot(positionViewDirection))).pow(power);

/** Draws `rgb` with `alpha`; an outer skin keeps only its edge band, which writes depth. */
function edged(rgb: THREE.Node<'vec3'>, alpha: THREE.Node<'float'>, outer: boolean): THREE.Node<'vec4'> {
  return Fn(() => {
    if (outer) {
      If(alpha.lessThan(EDGE_CUT), () => {
        Discard();
      });
    }
    return vec4(rgb, alpha);
  })();
}

export function tubeGlass(o: { face?: number; edge?: number; tint?: string; side?: THREE.Side } = {}): THREE.MeshPhysicalNodeMaterial {
  const outer = (o.side ?? THREE.FrontSide) !== THREE.BackSide;
  const tint = new THREE.Color(o.tint ?? '#e8f8ff');
  const mat = followEnvironment(new THREE.MeshPhysicalNodeMaterial({
    transparent: true, roughness: 0.06, metalness: 0, ior: 1.47,
    specularIntensity: 1, clearcoat: 1, clearcoatRoughness: 0.04, depthWrite: outer, side: o.side ?? THREE.FrontSide,
  }), 1.7);
  // Linear in the grazing term: a steep falloff would leave a side-on tube hairline edges only.
  const alpha = mix(float(o.face ?? 0.07), float(o.edge ?? 0.6), grazing(1));
  mat.colorNode = edged(vec3(tint.r, tint.g, tint.b), alpha, outer);
  return mat;
}

/** An unlit twin of a glass mesh for light paper: the same geometry, gray at its edges. */
export function paperTwin(mesh: THREE.Mesh): THREE.Mesh {
  const back = (mesh.material as THREE.Material).side === THREE.BackSide;
  const mat = new THREE.MeshBasicNodeMaterial({ transparent: true, depthWrite: !back, fog: false, side: back ? THREE.BackSide : THREE.FrontSide });
  const alpha = mix(float(back ? 0.03 : 0.07), float(back ? 0.25 : 0.55), grazing(0.9));
  mat.colorNode = edged(vec3(0.2, 0.25, 0.31), alpha, !back);
  const twin = new THREE.Mesh(mesh.geometry, mat);
  twin.position.copy(mesh.position);
  twin.quaternion.copy(mesh.quaternion);
  twin.scale.copy(mesh.scale);
  twin.renderOrder = mesh.renderOrder;
  twin.userData.occluder = false;
  return twin;
}
