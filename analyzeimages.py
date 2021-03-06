r"""Analyze Traffic Images

This executable is used to annotate traffic images to highlight vehicle types and to produce stats
and graphs for the amount of time bicycle lanes and bus stops are blocked by vehicles:


Example usage:
    ./analyzeimages \
        -path_images /tmp/preprocessed
        -path_labels_map data/car_label_map.pbtxt
        -save_directory /tmp/processed
"""

import sys

sys.path.append('./models-master/research/')
from object_detection.utils import label_map_util
from object_detection.utils import visualization_utils as vis_util
import boto3
import argparse
from argparse import RawTextHelpFormatter
import time
import random
import numpy as np
from saveimages import *
import os
import tensorflow as tf
import csv
from datetime import datetime
import matplotlib.pyplot as plt
import numpy as np
from collections import defaultdict
from io import StringIO
import matplotlib.path as mpltPath
save_to_aws = True
ACCESS_KEY = ""
SECRET_KEY = ""

from PIL import Image
import scipy.misc

DETECTION_LIMIT = .4


class TrafficResult:
    timestamp = 0
    cameraLocationId = 0
    numberCars = 0
    numberTrucks = 0

class AnalyzeImages:
    global table
    table = None

    def createGraph(self):
        pathcpkt = './faster_rcnn_resnet50_coco_2018_01_28/frozen_inference_graph.pb'
        detection_graph = tf.Graph()
        with detection_graph.as_default():
            od_graph_def = tf.GraphDef()
            with tf.gfile.GFile(pathcpkt, 'rb') as fid:
                serialized_graph = fid.read()
                od_graph_def.ParseFromString(serialized_graph)
                tf.import_graph_def(od_graph_def, name='')
        return detection_graph


    def createCategoryIndex(self,path_labels_map):
        num_classes = 6
        label_map = label_map_util.load_labelmap(path_labels_map)
        categories = label_map_util.convert_label_map_to_categories(label_map, max_num_classes=num_classes,
                                                                    use_display_name=True)
        return label_map_util.create_category_index(categories)

    def load_image_into_numpy_array(self,imageconvert):
        (im_width, im_height) = imageconvert.size
        try:
            return np.array(imageconvert.getdata()).reshape((im_height, im_width, 3)).astype(np.uint8)
        except ValueError:
            return np.array([])

    def saveAnnotatedImage(self,fileName,filePath,s3directory):
        return SaveImages().saveFileToS3(filePath,fileName,s3directory,False,ACCESS_KEY,SECRET_KEY)

    def getDatabaseInstance(self):
        global table
        if table != None:
            return table
        session = boto3.Session(
            aws_access_key_id=ACCESS_KEY,
            aws_secret_access_key=SECRET_KEY,
            region_name="us-east-1"
        )
        dynamodb = session.resource('dynamodb')
        table = dynamodb.Table('ourcamera')
        return table

    def logTrafficResult(self, trafficResult):
        if not save_to_aws:
            return
        assert isinstance(trafficResult, TrafficResult)
        self.getDatabaseInstance().put_item(
            Item={
                'timestamp': str(trafficResult.timestamp),
                'cameraLocationId':trafficResult.cameraLocationId,
                'cars': trafficResult.numberCars,
                'trucks': trafficResult.numberTrucks
            }
        )

    def processimages(self,path_images_dir, path_labels_map,save_directory):
        detection_graph = self.createGraph()
        category_index = self.createCategoryIndex(path_labels_map)

        with detection_graph.as_default():
            with tf.Session(graph=detection_graph) as sess:
                image_tensor = detection_graph.get_tensor_by_name('image_tensor:0')
                detection_boxes = detection_graph.get_tensor_by_name('detection_boxes:0')
                detection_scores = detection_graph.get_tensor_by_name('detection_scores:0')
                detection_classes = detection_graph.get_tensor_by_name('detection_classes:0')
                num_detections = detection_graph.get_tensor_by_name('num_detections:0')

                while(True):
                    for testpath in os.listdir(path_images_dir):
                        start_time = time.time()
                        timestamp,locationId = SaveImages().getTimestampAndLocationId(testpath)
                        if timestamp == 0:
                            os.remove(path_images_dir +"/"+ testpath)
                            continue
                        numCars = 0
                        numTrucks = 0

                        try:
                            image = Image.open(path_images_dir + '/' + testpath)
                            image_np = self.load_image_into_numpy_array(image)
                        except IOError:
                            print("Issue opening "+testpath)
                            os.remove(path_images_dir + testpath)
                            continue

                        if image_np.size == 0:
                            print("Skipping image "+testpath)
                            os.remove(path_images_dir + testpath)
                            continue

                        # Expand dimensions since the model expects images to have shape: [1, None, None, 3]
                        image_np_expanded = np.expand_dims(image_np, axis=0)
                        # Actual detection.
                        (boxes, scores, classes, num) = sess.run(
                            [detection_boxes, detection_scores, detection_classes, num_detections],
                            feed_dict={image_tensor: image_np_expanded})

                        scores = np.squeeze(scores)
                        boxes = np.squeeze(boxes)
                        for i in range(boxes.shape[0]):
                            if scores[i] > DETECTION_LIMIT:
                                box = tuple(boxes[i].tolist())

                                classes = np.squeeze(classes).astype(np.int32)
                                if classes[i] in category_index.keys():
                                    class_name = category_index[classes[i]]['name']
                                    if class_name == 'car':
                                        numCars=numCars+1;
                                    elif class_name == 'truck':
                                        numTrucks=numTrucks+1;

                        trafficResults = TrafficResult()
                        trafficResults.numberCars = numCars
                        trafficResults.numberTrucks = numTrucks
                        trafficResults.timestamp = timestamp
                        trafficResults.cameraLocationId =locationId
                        self.logTrafficResult(trafficResults)

                        print("Process Time " + str(time.time() - start_time))
                        print("There are "+str(numCars)+" cars and "+str(numTrucks)+" trucks/others");
                        if (random.randint(0,100)==1):
                            # Visualization of the results of a detection.
                            vis_util.visualize_boxes_and_labels_on_image_array(
                                image_np,
                                np.squeeze(boxes),
                                np.squeeze(classes).astype(np.int32),
                                np.squeeze(scores),
                                category_index,
                                min_score_thresh=0.4,
                                use_normalized_coordinates=True,
                                line_thickness=2)
                            print("save_directory "+save_directory)
                            print("testpath " + testpath)
                            scipy.misc.imsave(save_directory +"/"+ testpath, image_np)
                            self.saveAnnotatedImage(testpath,save_directory +"/"+ testpath,"annotated")
                        os.remove(path_images_dir + '/' + testpath)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Analyze traffic images to determine rate of blocking bike'
                    'and bus lanes', formatter_class=RawTextHelpFormatter)
    parser.add_argument('-path_images', help='the folder with all the downloaded images in it')
    parser.add_argument('-path_labels_map', help='the file with the integer to label map')
    parser.add_argument('-save_directory', help='the directory you want to save the annotated images to')
    parser.add_argument('-access_key', help='aws access key')
    parser.add_argument('-secret_key', help='aws secret key')
    args = parser.parse_args()
    SaveImages().mkdir_p(args.save_directory)
    ACCESS_KEY = args.access_key
    SECRET_KEY = args.secret_key
    AnalyzeImages().processimages(args.path_images,args.path_labels_map,args.save_directory)
