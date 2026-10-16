{
 "patcher": {
  "fileversion": 1,
  "appversion": {
   "major": 9,
   "minor": 0,
   "revision": 0,
   "architecture": "x64",
   "modernui": 1
  },
  "classnamespace": "box",
  "rect": [
   80.0,
   80.0,
   760.0,
   480.0
  ],
  "boxes": [
   {
    "box": {
     "id": "obj-1",
     "maxclass": "comment",
     "text": "SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors. SPDX-License-Identifier: Apache-2.0",
     "numinlets": 1,
     "numoutlets": 0,
     "patching_rect": [
      20,
      10,
      700,
      22.0
     ]
    }
   },
   {
    "box": {
     "id": "obj-2",
     "maxclass": "comment",
     "text": "nxlx render node: Mastercontrol's /nxlx/ cues in on UDP 7400, a placeholder Jitter look, its picture out as NDI. Untested in Max: see tools/max/README.md",
     "numinlets": 1,
     "numoutlets": 0,
     "patching_rect": [
      20,
      34,
      700,
      22.0
     ]
    }
   },
   {
    "box": {
     "id": "obj-3",
     "maxclass": "comment",
     "text": "Needs jit.ndi by 2bbb (github.com/2bbb/jit.ndi), installed by you in Max 9: it is not part of this repository.",
     "numinlets": 1,
     "numoutlets": 0,
     "patching_rect": [
      20,
      58,
      700,
      22.0
     ]
    }
   },
   {
    "box": {
     "id": "obj-4",
     "maxclass": "newobj",
     "text": "udpreceive 7400",
     "numinlets": 1,
     "numoutlets": 1,
     "patching_rect": [
      20,
      100,
      160,
      22.0
     ],
     "outlettype": [
      ""
     ]
    }
   },
   {
    "box": {
     "id": "obj-5",
     "maxclass": "newobj",
     "text": "print nxlx-in",
     "numinlets": 1,
     "numoutlets": 0,
     "patching_rect": [
      220,
      100,
      100,
      22.0
     ]
    }
   },
   {
    "box": {
     "id": "obj-6",
     "maxclass": "newobj",
     "text": "route /nxlx/scene /nxlx/param /nxlx/color /nxlx/go /nxlx/hello",
     "numinlets": 1,
     "numoutlets": 6,
     "patching_rect": [
      20,
      140,
      420,
      22.0
     ],
     "outlettype": [
      "",
      "",
      "",
      "",
      "",
      ""
     ]
    }
   },
   {
    "box": {
     "id": "obj-7",
     "maxclass": "newobj",
     "text": "% 4",
     "numinlets": 2,
     "numoutlets": 1,
     "patching_rect": [
      20,
      190,
      50,
      22.0
     ],
     "outlettype": [
      "int"
     ]
    }
   },
   {
    "box": {
     "id": "obj-8",
     "maxclass": "newobj",
     "text": "sel 0 1 2 3",
     "numinlets": 1,
     "numoutlets": 5,
     "patching_rect": [
      20,
      220,
      120,
      22.0
     ],
     "outlettype": [
      "bang",
      "bang",
      "bang",
      "bang",
      ""
     ]
    }
   },
   {
    "box": {
     "id": "obj-9",
     "maxclass": "message",
     "text": "shape sphere",
     "numinlets": 2,
     "numoutlets": 1,
     "patching_rect": [
      20,
      255,
      85,
      22.0
     ],
     "outlettype": [
      ""
     ]
    }
   },
   {
    "box": {
     "id": "obj-10",
     "maxclass": "message",
     "text": "shape torus",
     "numinlets": 2,
     "numoutlets": 1,
     "patching_rect": [
      110,
      255,
      85,
      22.0
     ],
     "outlettype": [
      ""
     ]
    }
   },
   {
    "box": {
     "id": "obj-11",
     "maxclass": "message",
     "text": "shape cube",
     "numinlets": 2,
     "numoutlets": 1,
     "patching_rect": [
      200,
      255,
      85,
      22.0
     ],
     "outlettype": [
      ""
     ]
    }
   },
   {
    "box": {
     "id": "obj-12",
     "maxclass": "message",
     "text": "shape plane",
     "numinlets": 2,
     "numoutlets": 1,
     "patching_rect": [
      290,
      255,
      85,
      22.0
     ],
     "outlettype": [
      ""
     ]
    }
   },
   {
    "box": {
     "id": "obj-13",
     "maxclass": "newobj",
     "text": "counter 0 3",
     "numinlets": 5,
     "numoutlets": 4,
     "patching_rect": [
      450,
      190,
      90,
      22.0
     ],
     "outlettype": [
      "int",
      "",
      "",
      "int"
     ]
    }
   },
   {
    "box": {
     "id": "obj-14",
     "maxclass": "newobj",
     "text": "route scale spin",
     "numinlets": 1,
     "numoutlets": 3,
     "patching_rect": [
      200,
      190,
      120,
      22.0
     ],
     "outlettype": [
      "",
      "",
      ""
     ]
    }
   },
   {
    "box": {
     "id": "obj-15",
     "maxclass": "newobj",
     "text": "t f f f",
     "numinlets": 1,
     "numoutlets": 3,
     "patching_rect": [
      200,
      220,
      60,
      22.0
     ],
     "outlettype": [
      "float",
      "float",
      "float"
     ]
    }
   },
   {
    "box": {
     "id": "obj-16",
     "maxclass": "newobj",
     "text": "pack f f f",
     "numinlets": 3,
     "numoutlets": 1,
     "patching_rect": [
      200,
      250,
      70,
      22.0
     ],
     "outlettype": [
      ""
     ]
    }
   },
   {
    "box": {
     "id": "obj-17",
     "maxclass": "newobj",
     "text": "prepend scale",
     "numinlets": 1,
     "numoutlets": 1,
     "patching_rect": [
      200,
      280,
      90,
      22.0
     ],
     "outlettype": [
      ""
     ]
    }
   },
   {
    "box": {
     "id": "obj-18",
     "maxclass": "newobj",
     "text": "* 360.",
     "numinlets": 2,
     "numoutlets": 1,
     "patching_rect": [
      300,
      220,
      60,
      22.0
     ],
     "outlettype": [
      "float"
     ]
    }
   },
   {
    "box": {
     "id": "obj-19",
     "maxclass": "newobj",
     "text": "prepend rotatexyz 0. 0.",
     "numinlets": 1,
     "numoutlets": 1,
     "patching_rect": [
      300,
      250,
      150,
      22.0
     ],
     "outlettype": [
      ""
     ]
    }
   },
   {
    "box": {
     "id": "obj-20",
     "maxclass": "newobj",
     "text": "route bg shape",
     "numinlets": 1,
     "numoutlets": 3,
     "patching_rect": [
      470,
      230,
      110,
      22.0
     ],
     "outlettype": [
      "",
      "",
      ""
     ]
    }
   },
   {
    "box": {
     "id": "obj-21",
     "maxclass": "newobj",
     "text": "prepend erase_color",
     "numinlets": 1,
     "numoutlets": 1,
     "patching_rect": [
      470,
      260,
      130,
      22.0
     ],
     "outlettype": [
      ""
     ]
    }
   },
   {
    "box": {
     "id": "obj-22",
     "maxclass": "newobj",
     "text": "prepend color",
     "numinlets": 1,
     "numoutlets": 1,
     "patching_rect": [
      610,
      260,
      100,
      22.0
     ],
     "outlettype": [
      ""
     ]
    }
   },
   {
    "box": {
     "id": "obj-23",
     "maxclass": "newobj",
     "text": "print nxlx-hello",
     "numinlets": 1,
     "numoutlets": 0,
     "patching_rect": [
      600,
      190,
      110,
      22.0
     ]
    }
   },
   {
    "box": {
     "id": "obj-24",
     "maxclass": "newobj",
     "text": "jit.world nxlx @enable 1 @fps 30 @output_matrix 1 @erase_color 0. 0. 0. 1.",
     "numinlets": 1,
     "numoutlets": 3,
     "patching_rect": [
      20,
      330,
      420,
      22.0
     ],
     "outlettype": [
      "",
      "",
      ""
     ]
    }
   },
   {
    "box": {
     "id": "obj-25",
     "maxclass": "newobj",
     "text": "jit.gl.gridshape nxlx @shape torus @color 1. 0.45 0.1 1. @lighting_enable 1",
     "numinlets": 1,
     "numoutlets": 2,
     "patching_rect": [
      20,
      370,
      420,
      22.0
     ],
     "outlettype": [
      "",
      ""
     ]
    }
   },
   {
    "box": {
     "id": "obj-26",
     "maxclass": "newobj",
     "text": "jit.ndi.send~ @name nxlx-render-node",
     "numinlets": 1,
     "numoutlets": 0,
     "patching_rect": [
      20,
      420,
      260,
      22.0
     ]
    }
   }
  ],
  "lines": [
   {
    "patchline": {
     "source": [
      "obj-4",
      0
     ],
     "destination": [
      "obj-5",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-4",
      0
     ],
     "destination": [
      "obj-6",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-6",
      0
     ],
     "destination": [
      "obj-7",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-7",
      0
     ],
     "destination": [
      "obj-8",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-8",
      0
     ],
     "destination": [
      "obj-9",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-8",
      1
     ],
     "destination": [
      "obj-10",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-8",
      2
     ],
     "destination": [
      "obj-11",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-8",
      3
     ],
     "destination": [
      "obj-12",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-6",
      3
     ],
     "destination": [
      "obj-13",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-13",
      0
     ],
     "destination": [
      "obj-7",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-6",
      1
     ],
     "destination": [
      "obj-14",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-14",
      0
     ],
     "destination": [
      "obj-15",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-15",
      0
     ],
     "destination": [
      "obj-16",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-15",
      1
     ],
     "destination": [
      "obj-16",
      1
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-15",
      2
     ],
     "destination": [
      "obj-16",
      2
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-16",
      0
     ],
     "destination": [
      "obj-17",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-14",
      1
     ],
     "destination": [
      "obj-18",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-18",
      0
     ],
     "destination": [
      "obj-19",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-6",
      2
     ],
     "destination": [
      "obj-20",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-20",
      0
     ],
     "destination": [
      "obj-21",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-20",
      1
     ],
     "destination": [
      "obj-22",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-6",
      4
     ],
     "destination": [
      "obj-23",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-21",
      0
     ],
     "destination": [
      "obj-24",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-9",
      0
     ],
     "destination": [
      "obj-25",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-10",
      0
     ],
     "destination": [
      "obj-25",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-11",
      0
     ],
     "destination": [
      "obj-25",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-12",
      0
     ],
     "destination": [
      "obj-25",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-17",
      0
     ],
     "destination": [
      "obj-25",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-19",
      0
     ],
     "destination": [
      "obj-25",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-22",
      0
     ],
     "destination": [
      "obj-25",
      0
     ]
    }
   },
   {
    "patchline": {
     "source": [
      "obj-24",
      0
     ],
     "destination": [
      "obj-26",
      0
     ]
    }
   }
  ],
  "description": "nxlx render node (D94). SPDX-License-Identifier: Apache-2.0",
  "tags": "nxlx mastercontrol render-node"
 }
}