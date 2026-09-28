#include <opencv2/opencv.hpp>
#include <dlfcn.h>
#include <opencv2/core/bmcv.hpp>
#include <vector>
#include <iostream>
#include <atomic>
#include <stdexcept>
static std::atomic<unsigned long> calls[3];
static std::atomic<int> phase{0};
extern "C" int bm_trigger_vpp(void* h,void* batch){
 using F=int(*)(void*,void*); static F f=(F)dlsym(RTLD_NEXT,"bm_trigger_vpp");
 if(!f)std::abort();++calls[phase.load()];return f(h,batch);
}
static bool save_pixels(cv::Mat& frame,const std::string& path){
 bm_handle_t h=nullptr;bm_image in{},out{};
 if(bm_dev_request(&h,0)!=BM_SUCCESS)return false;
 int stride[]={256*3};std::vector<unsigned char> pixels(256*144*3);void* buffers[]={pixels.data()};
 if(cv::bmcv::toBMI(frame,&in,false)!=BM_SUCCESS)std::abort();
 if(bm_image_create(h,144,256,FORMAT_BGR_PACKED,DATA_TYPE_EXT_1N_BYTE,&out,stride)!=BM_SUCCESS)std::abort();
 if(bm_image_alloc_dev_mem(out,BMCV_IMAGE_FOR_OUT)!=BM_SUCCESS)std::abort();
 if(bmcv_image_vpp_convert(h,1,in,&out)!=BM_SUCCESS)std::abort();
 if(bm_image_copy_device_to_host(out,buffers)!=BM_SUCCESS)std::abort();
 cv::Mat host(144,256,CV_8UC3,pixels.data());bool ok=cv::imwrite(path,host);
 bm_image_destroy(out);bm_dev_free(h);return ok;
}
int main(int argc,char**argv){
 if(argc!=4 && argc!=5)return 2;int step=std::stoi(argv[2]),n=std::stoi(argv[3]);
 if(step<1 || n<1)return 2;
 cv::VideoCapture cap;if(!cap.open(argv[1],cv::CAP_FFMPEG,0))return 3;
 if(!cap.set(cv::CAP_PROP_OUTPUT_YUV,1))return 4;
 int grabs=0,retrieves=0;
 for(int i=0;i<n;++i){phase=1;if(!cap.grab())return 5;++grabs;
 if(i%step==0){phase=2;cv::Mat frame;if(!cap.retrieve(frame)||frame.empty())return 6;++retrieves;phase=0;if(argc==5 && !save_pixels(frame,std::string(argv[4])+"/"+std::to_string(i)+".png"))return 7;}phase=0;}
 std::cout<<"RESULT grabs="<<grabs<<" retrieves="<<retrieves<<" grab_vpp="<<calls[1]<<" retrieve_vpp="<<calls[2]<<" other_vpp="<<calls[0]<<std::endl;
}