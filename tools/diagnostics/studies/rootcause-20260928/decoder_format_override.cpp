// Diagnostic-only decoder configuration ablation; no installed file replaced.
#include <dlfcn.h>
#include <stdint.h>
#include <cstdio>
#include <cstdlib>
#include <cstring>
struct AVDictionary;
extern "C" int av_dict_set_int(AVDictionary** pm,const char* key,int64_t value,int flags){
    using F=int(*)(AVDictionary**,const char*,int64_t,int);
    static F real=reinterpret_cast<F>(dlsym(RTLD_NEXT,"av_dict_set_int"));
    if(!real){fprintf(stderr,"missing av_dict_set_int\n");abort();}
    const char* setting=getenv("VPP_DIAG_OUTPUT_FORMAT");
    if(setting && key && !std::strcmp(key,"output_format")){
        if(std::strcmp(setting,"0") && std::strcmp(setting,"101")){fprintf(stderr,"invalid diagnostic output format\n");abort();}
        int64_t effective=std::strcmp(setting,"0")==0?0:101;
        fprintf(stderr,"VPP_DIAG output_format requested=%lld effective=%lld\n",(long long)value,(long long)effective);
        value=effective;
    }
    return real(pm,key,value,flags);
}
