# ATTENTION HERE: since the dataset has some some problem, i have made some modification in the dataset,
# So if want to transfer the code to another machine, it is necessary to transfer the data meanwhile.
import os
import sys
import json
import numpy as np
from plyfile import PlyData

sys.path.append(os.path.join(os.getcwd(), "lib"))   # HACK add the lib folder
from config import CONF
import multiprocessing

lock = multiprocessing.Lock()

def read_ply(filename):
    """ read point cloud from filename PLY file """
    plydata = PlyData.read(filename)
    pc = plydata['vertex'].data
    pc_array = np.array([[x, y, z, r, g, b, oid, cid, nyu, mpr] for x, y, z, r, g, b, oid, cid, nyu, mpr in pc])
    return pc_array

def read_obj(filename):
    """ read point cloud from OBJ file"""
    with open(filename) as file:
        point_cloud = []
        while 1:
            line = file.readline()
            if not line:
                break
            strs = line.split(" ")
            if strs[0] == "v":
                point_cloud.append((float(strs[1]), float(strs[2]), float(strs[3])))
        point_cloud = np.array(point_cloud)
    return point_cloud

def pc_normalize(pc):
    pc_ = pc[:,:3]
    centroid = np.mean(pc_, axis=0)
    pc_ = pc_ - centroid
    m = np.max(np.sqrt(np.sum(pc_ ** 2, axis=1)))
    pc_ = pc_ / m
    if pc.shape[1] > 3:
        pc = np.concatenate((pc_, pc[:,3].reshape(-1,1)), axis=1)
    else:
        pc = pc_
    return pc

def farthest_point_sample(point, npoint):
    """
    Input:
        xyz: pointcloud data, [N, D]
        npoint: number of samples
    Return:
        centroids: sampled pointcloud index, [npoint, D]
    """
    N, D = point.shape
    if N < npoint:
        return point
    xyz = point[:, :3]
    centroids = np.zeros((npoint,))
    distance = np.ones((N,)) * 1e10
    farthest = np.random.randint(0, N)
    for i in range(npoint):
        centroids[i] = farthest
        centroid = xyz[farthest, :]
        dist = np.sum((xyz - centroid) ** 2, -1)
        mask = dist < distance
        distance[mask] = dist[mask]
        farthest = np.argmax(distance, -1)
    point = point[centroids.astype(np.int32)]
    return point

def judge_obb_intersect(p, obb):
    # judge one point is or not in the obb
    center = np.array(obb["centroid"])
    axis_len = np.array(obb["axesLengths"])
    axis_x = np.array(obb["normalizedAxes"][0:3])
    axis_y = np.array(obb["normalizedAxes"][3:6])
    axis_z = np.array(obb["normalizedAxes"][6:9])
    project_x = axis_x.dot(p - center)
    project_y = axis_y.dot(p - center)
    project_z = axis_z.dot(p - center)
    return -axis_len[0]/2 <= project_x <= axis_len[0]/2 and\
           -axis_len[1]/2 <= project_y <= axis_len[1]/2 and\
           -axis_len[2]/2 <= project_z <= axis_len[2]/2

def process_one_scan(relationships_scan):
    scan_id = relationships_scan["scan"] + "-" + str(hex(relationships_scan["split"]))[-1]

    # avoid duplicate computing
    path = os.path.join(CONF.PATH.R3Scan, "{}/data_dict_{}.json".format(scan_id[:-2], scan_id[-1]))
    if os.path.exists(path):
        print(path," already exits!")
        return

    # load class and relationships dict
    word2idx = {}
    index = 0
    file = open(os.path.join(CONF.PATH.DATA, "3DSSG_subset/classes.txt"), 'r')
    category = file.readline()[:-1]
    while category:
        word2idx[category] = index  # {物体类别名称：索引}
        category = file.readline()[:-1]
        index += 1

    rel2idx = {}
    index = 0
    file = open(os.path.join(CONF.PATH.DATA, "3DSSG_subset/relationships.txt"), 'r')
    category = file.readline()[:-1]
    while category:
        rel2idx[category] = index
        category = file.readline()[:-1]
        index += 1

    # read point cloud from OBJ file
    scan = scan_id[:-2]
    pc_array = read_obj(os.path.join(CONF.PATH.R3Scan, "{}/mesh.refined.v2.obj".format(scan)))  # 所有点的坐标
    # group points in the same segment
    segments = {}  # key:segment id, value: points belong to this segment
    with open(os.path.join(CONF.PATH.R3Scan, "{}/mesh.refined.0.010000.segs.v2.json".format(scan)), 'r') as f:
        seg_indices = json.load(f)["segIndices"]
        for index, i in enumerate(seg_indices):  # pc_array中第index个点对应segment中的第i个点（segment是对点云进行一个小的分组，一个分组中点的label相同，这样方便后续处理）
            if i not in segments:
                segments[i] = []
            segments[i].append(pc_array[index])

    # group points of the same object
    # filter the object which does not belong to this split
    obj_id_list = []
    for k, _ in relationships_scan["objects"].items():
        obj_id_list.append(int(k))

    with open(os.path.join(CONF.PATH.R3Scan, "{}/semseg.v2.json".format(scan)), 'r') as f:
        seg_groups = json.load(f)["segGroups"]
        objects = {}  # object mapping to its belonging points
        obb = {}  # obb in this scan split, size equals objects num
        labels = {}  # { id: 'category name', 6:'trash can'}
        seg2obj = {}  # mapping between segment and object id
        for o in seg_groups:
            id = o["id"]
            if id not in obj_id_list:  # no corresponding relationships in this split
                continue
            if o["label"] not in word2idx:  # Categories not under consideration
                continue
            labels[id] = o["label"]
            segs = o["segments"]
            objects[id] = []
            obb[id] = o["obb"]
            for i in segs:
                seg2obj[i] = id  # 构造segment
                for j in segments[i]:
                    objects[id] = j.reshape(1, -1) if len(objects[id]) == 0 else np.concatenate((objects[id], j.reshape(1, -1)), axis=0)
    # sample and normalize point cloud
    obj_sample = CONF.SCALAR.OBJ_PC_SAMPLE
    for obj_id, obj_pc in objects.items():
        pc = farthest_point_sample(obj_pc, obj_sample)
        objects[obj_id] = pc_normalize(pc)

    objects_id = []
    objects_cat = []
    objects_pc = []
    objects_num = []
    for k, v in objects.items():
        objects_id.append(k)
        objects_cat.append(word2idx[labels[k]])
        objects_num = objects_num + [len(v)]
        objects_pc = v if not len(objects_pc) else np.concatenate((objects_pc, v), axis=0)

    # predicate input of PointNet, including points in the union bounding box of subject and object
    # here consider every possible combination between objects, if there doesn't exist relation in the training file,
    # add the relation with the predicate id replaced by 0
    # []
    triples = []
    pairs = []
    relationships_triples = relationships_scan["relationships"]  #[object_1, object_2, class index of predicate, class name of predicate]
    for triple in relationships_triples:
        if (triple[0] not in objects_id) or (triple[1] not in objects_id) or (triple[0] == triple[1]):
            continue
        triples.append(triple[:3])
        if triple[:2] not in pairs:
            pairs.append(triple[:2])
    for i in objects_id:
        for j in objects_id:
            if i == j or [i, j] in pairs:
                continue
            triples.append([i, j, 0])   # supplement the 'none' relation
            pairs.append(([i, j]))

    s = 0
    o = 0
    try:
        union_point_cloud = []
        predicate_cat = []
        predicate_num = []
        for rel in pairs:
            s, o = rel
            union_pc = []
            pred_cls = np.zeros(len(rel2idx))
            for triple in triples:
                if rel == triple[:2]:
                    pred_cls[triple[2]] = 1

            for index, point in enumerate(pc_array):
                if seg_indices[index] not in seg2obj:
                    continue
                # union box 是sub和obj的bbox的直接拼接（可以尝试改成构造大bbox将二者都框进去）
                if judge_obb_intersect(point, obb[s]) or judge_obb_intersect(point, obb[o]):  
                    if seg2obj[seg_indices[index]] == s:
                        point = np.append(point, 1)
                    elif seg2obj[seg_indices[index]] == o:
                        point = np.append(point, 2)
                    else:
                        point = np.append(point, 0)
                    # 此时的union_pc为4维，其中有一维是表示是subject还是object还是虽然点在bbox中但不属于sub和obj
                    union_pc.append(point)
            union_point_cloud.append(union_pc)  # 联合pointcloud的点云坐标
            predicate_cat.append(pred_cls.tolist())
        # sample and normalize point cloud
        rel_sample = CONF.SCALAR.REL_PC_SAMPLE  # 最远点采样到3000个点
        for index, _ in enumerate(union_point_cloud):
            pc = np.array(union_point_cloud[index])
            pc = farthest_point_sample(pc, rel_sample)
            union_point_cloud[index] = pc_normalize(pc)
            predicate_num.append(len(pc))
    except KeyError:
        print(scan_id)
        print(obb.keys())
        print(s, o, '\n')
        return

    predicate_pc_flag = []
    for pc in union_point_cloud:
        predicate_pc_flag = pc if len(predicate_pc_flag) == 0 else np.concatenate((predicate_pc_flag, pc), axis=0)

    object_id2idx = {}  # convert object id to the index in the tensor
    for index, v in enumerate(objects_id):
        object_id2idx[v] = index
    s, o = np.split(np.array(pairs), 2, axis=1)  # All have shape (T, 1)
    s, o = [np.squeeze(x, axis=1) for x in [s, o]]  # Now have shape (T,)

    for index, v in enumerate(s):
        s[index] = object_id2idx[v]  # s_idx
    for index, v in enumerate(o):
        o[index] = object_id2idx[v]  # o_idx
    edges = np.stack((s, o), axis=1)    # edges is used for the input of the GCN module

    # # since point cloud in 3DSGG has been processed, there is no need to sample any more => actually need
    # point_cloud, choices = random_sampling(point_cloud, self.num_points, return_choices=True)

    data_dict = {}
    data_dict["scan_id"] = scan_id
    data_dict["objects_id"] = objects_id  # object id
    data_dict["objects_cat"] = objects_cat  # object category
    data_dict["objects_num"] = objects_num
    data_dict["objects_pc"] = objects_pc.tolist()  # corresponding point cloud
    data_dict["predicate_cat"] = predicate_cat  # predicate id
    data_dict["predicate_num"] = predicate_num
    data_dict["predicate_pc_flag"] = predicate_pc_flag.tolist()  # corresponding point cloud in the union bounding box
    data_dict["pairs"] = pairs
    data_dict["edges"] = edges.tolist()
    data_dict["triples"] = triples
    data_dict["objects_count"] = len(objects_cat)
    data_dict["predicate_count"] = len(predicate_cat)

    return data_dict

def write_into_json(relationship):
    data_dict = process_one_scan(relationship)
    if data_dict is None:
        return

    # process needs lock to write into disk
    lock.acquire()
    scan_id = data_dict["scan_id"]
    path = os.path.join(CONF.PATH.R3Scan, "{}/data_dict_{}.json".format(scan_id[:-2], scan_id[-1]))
    print("{}/data_dict_{}.json".format(scan_id[:-2], scan_id[-1]))
    with open(path, 'w') as f:
        f.write(json.dumps(data_dict, indent=4))
    lock.release()

# def strip_file(old_file, new_file):
#     """remove the space or Tab or enter in a file, and output to a new file in the same folder"""
#     fp = open(old_file, 'r+')
#     newfp = open(new_file, 'w')
#     for line in fp.readlines():
#         str = line.replace(" ", "").replace("\t", "").strip()
#         newfp.write(str)
#     fp.close()
#     newfp.close()

if __name__ == '__main__':
    relationships_train = json.load(open(os.path.join(CONF.PATH.DATA, "3DSSG_subset/relationships_train.json")))["scans"]
    relationships_val = json.load(open(os.path.join(CONF.PATH.DATA, "3DSSG_subset/relationships_validation.json")))["scans"]

    # merge two dicts
    relationships = relationships_train + relationships_val
    # CONF.PATH.R3Scan = '/data/liyifan/3dssg-master/3RScan_test'
    # relationships = json.load(open(os.path.join(CONF.PATH.DATA, "3DSSG_subset/fix.json")))["scans"]

    pool = multiprocessing.Pool(multiprocessing.cpu_count())
    pool.map(write_into_json, relationships)
    pool.close()
    pool.join()

    # for relationship in relationships:
    #     path = os.path.join(CONF.PATH.R3Scan, "{}/mini_data_dict_{}.json".format(relationship["scan"], str(hex(relationship["split"]))[-1]))
    #     if os.path.exists(path):
    #         os.remove(path)